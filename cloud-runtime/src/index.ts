/**
 * Festival Intelligence Acquisition Runtime — Worker Entry Point.
 *
 * Exports:
 * - default: fetch + queue handlers
 * - AcquisitionGovernor: Durable Object
 * - AcquisitionContainer: Container-enabled Durable Object
 * - AcquisitionWorkflow: Workflow entrypoint
 */

import { AcquisitionGovernor } from "./governor-do";
import { AcquisitionContainer } from "./container-do";
import { AcquisitionWorkflow } from "./workflow";
import { handleFastBatch, handleDeepBatch, handleProcessingBatch } from "./queue-consumer";

export { AcquisitionGovernor, AcquisitionContainer, AcquisitionWorkflow };

interface Env {
  FAST_QUEUE: Queue;
  DEEP_QUEUE: Queue;
  PROCESSING_QUEUE: Queue;
  DLQ_QUEUE: Queue;
  RAW_BUCKET: R2Bucket;
  LAKE_BUCKET: R2Bucket;
  BACKUP_BUCKET: R2Bucket;
  GOVERNOR: DurableObjectNamespace;
  ACQUISITION_CONTAINER: DurableObjectNamespace;
  ACQUISITION_WORKFLOW: Workflow;
  MONID_API_KEY: string;
  TICKETMASTER_API_KEY: string;
  TICKETS_DEV_API_KEY: string;
  FI_R2_ACCESS_KEY_ID: string;
  FI_R2_SECRET_ACCESS_KEY: string;
  FI_R2_RAW_BUCKET: string;
  POLICY_VERSION: string;
  SOFTWARE_VERSION: string;
  DAILY_BUDGET_USD: string;
  MONTHLY_BUDGET_USD: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      return Response.json({
        status: "ok",
        version: env.SOFTWARE_VERSION,
        policy_version: env.POLICY_VERSION,
      });
    }

    if (url.pathname === "/reset-governor" && request.method === "POST") {
      const governorId = env.GOVERNOR.idFromName("acquisition-governor");
      const governor = env.GOVERNOR.get(governorId) as any;
      await governor.resetState();
      return Response.json({ status: "governor_reset" });
    }

    if (url.pathname === "/governor") {
      const governorId = env.GOVERNOR.idFromName("acquisition-governor");
      const governor = env.GOVERNOR.get(governorId) as any;
      // RPC call — governor stub has typed methods
      const summary = await (governor as any).getReservationSummary();
      return Response.json(summary);
    }

    if (url.pathname === "/trigger" && request.method === "POST") {
      const instance = await env.ACQUISITION_WORKFLOW.create();
      return Response.json({ instance_id: instance.id, status: "triggered" });
    }

    if (url.pathname === "/test-fetch" && request.method === "POST") {
      // Direct test: fetch a marketplace page via Monid, write to R2.
      // Bypasses Governor/Queue to prove the Monid→R2 pipeline works.
      try {
        const body = await request.json() as { url: string; event_key?: string; marketplace?: string };
        if (!body.url) return Response.json({ error: "url required" }, { status: 400 });

        // Import monid-client inline to avoid module issues
        const { fetchPage, extractFromPage } = await import("./monid-client");
        const page = await fetchPage(env.MONID_API_KEY, body.url);

        if (page.status !== "FETCHED") {
          return Response.json({ error: page.status, latency_ms: page.latency_ms });
        }

        const extracted = extractFromPage(page.html, body.marketplace || "unknown");
        const now = new Date().toISOString();

        // Write raw evidence
        const htmlBytes = new TextEncoder().encode(page.html);
        const hashBuf = await crypto.subtle.digest("SHA-256", htmlBytes.buffer as ArrayBuffer);
        const contentHash = [...new Uint8Array(hashBuf)].map(b => b.toString(16).padStart(2, "0")).join("");
        const h0 = contentHash.slice(0, 2), h1 = contentHash.slice(2, 4);
        const ek = body.event_key || "test";
        const mp = body.marketplace || "unknown";
        const rawKey = `raw/monid/${h0}/${h1}/${contentHash}.json`;

        const rawPayload = JSON.stringify({ url: body.url, marketplace: mp, event_key: ek, provider: page.provider, html: page.html.slice(0, 50_000), extracted, fetched_at: now, cost_usd: page.cost_usd });
        await env.RAW_BUCKET.put(rawKey, rawPayload, {
          httpMetadata: { contentType: "application/json" },
          customMetadata: { source: "monid", marketplace: mp, event_key: ek, content_hash: contentHash },
        });

        // Write normalized observation to lake staging
        const observation = {
          schema_version: "ticket_market_snapshot_v1",
          event_key: ek, source_platform: mp,
          actor_or_endpoint: `monid_${page.provider}`,
          observed_at: now, retrieved_at: now, knowledge_time: now,
          currency: extracted.currency || null,
          resale_min_price: extracted.price ?? extracted.price_min ?? null,
          sold_out_flag: String(extracted.availability || "").toLowerCase().includes("soldout"),
          identity_match_status: "MATCHED",
          source_url: body.url, raw_payload_hash: contentHash,
          rights_status: "TERMS_REVIEW_REQUIRED",
          commercial_use_status: "PROTOTYPE_ONLY",
        };
        const stagingKey = `staging/ticket_market/date=${now.slice(0, 10)}/hour=${now.slice(11, 13)}/test-${Date.now()}.json`;
        await env.LAKE_BUCKET.put(stagingKey, JSON.stringify(observation, null, 2), {
          httpMetadata: { contentType: "application/json" },
        });

        return Response.json({
          status: "FETCHED_AND_STORED",
          raw_key: rawKey,
          staging_key: stagingKey,
          content_hash: contentHash,
          provider: page.provider,
          cost_usd: page.cost_usd,
          latency_ms: page.latency_ms,
          extracted,
        });
      } catch (e: any) {
        return Response.json({ error: e.message || String(e) }, { status: 500 });
      }
    }

    if (url.pathname === "/test-monid" && request.method === "POST") {
      // Diagnostic: call Monid API directly, inspect response structure
      try {
        const body = await request.json() as { url: string };
        const targetUrl = body.url;
        if (!targetUrl) return Response.json({ error: "url required" }, { status: 400 });

        const apiKey = env.MONID_API_KEY;
        const runResp = await fetch("https://api.monid.ai/v1/run", {
          method: "POST",
          headers: {
            Authorization: `Bearer ${apiKey}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            provider: "context.dev",
            endpoint: "/web/scrape/html",
            queryParams: { url: targetUrl },
          }),
        });

        const runText = await runResp.text();
        let runData: any;
        try { runData = JSON.parse(runText); } catch { runData = { raw: runText.slice(0, 500) }; }

        const runId = runData.runId || runData.run_id;
        const status = runData.status || "UNKNOWN";

        // Debug: what keys do we have?
        const debugKeys = Object.keys(runData);
        const outputKeys = runData.output ? Object.keys(runData.output) : [];
        const htmlLen = runData.output?.html?.length || 0;

        if (status === "COMPLETED" && htmlLen > 0) {
          const html = runData.output.html;
          return Response.json({
            status: "IMMEDIATE_COMPLETE",
            run_id: runId,
            html_length: html.length,
            has_json_ld: html.includes("application/ld+json"),
            has_structured: html.includes("schema.org"),
            output_keys: outputKeys,
            cost: runData.price || runData.cost || null,
          });
        }

        // Not complete yet — poll
        if (runId && status !== "FAILED" && status !== "ERROR") {
          await new Promise(r => setTimeout(r, 8000));
          const pollResp = await fetch(`https://api.monid.ai/v1/runs/${runId}`, {
            headers: { Authorization: `Bearer ${apiKey}` },
          });
          const pollText = await pollResp.text();
          let pollData: any;
          try { pollData = JSON.parse(pollText); } catch { pollData = { raw: pollText.slice(0, 500) }; }
          const pollHtmlLen = pollData.output?.html?.length || 0;
          return Response.json({
            status: pollData.status,
            run_id: runId,
            html_length: pollHtmlLen,
            has_json_ld: (pollData.output?.html || "").includes("application/ld+json"),
            output_keys: pollData.output ? Object.keys(pollData.output) : [],
            cost: pollData.price || pollData.cost || null,
          });
        }

        return Response.json({
          debug: true, status, run_id: runId, debug_keys: debugKeys, output_keys: outputKeys,
          html_len: htmlLen, response_status: runResp.status,
        });
      } catch (e: any) {
        return Response.json({ error: e.message || String(e) }, { status: 500 });
      }
    }

    if (url.pathname === "/run-pilot" && request.method === "POST") {
      // Direct pilot: fetch N events synchronously, no queue needed.
      // Proves the pipeline end-to-end for real acquisition.
      try {
        const body = await request.json() as { max_events?: number; max_cost?: number };
        const maxEvents = body.max_events || 5;
        const maxCost = body.max_cost || 0.01;
        const MONID_BASE = "https://api.monid.ai";
        const results: any[] = [];
        let totalCost = 0;
        let fetched = 0;

        // Load universe
        const universeObj = await env.BACKUP_BUCKET.get("canonical/2026-08-26T01-00-58Z/watch_universe_v1.json");
        if (!universeObj) return Response.json({ error: "No universe" }, { status: 500 });
        const universe: any = await universeObj.json();
        const events = universe.events || [];

        let attempts = 0;
        for (const ev of events) {
          if (fetched >= maxEvents) break;
          if (totalCost >= maxCost) break;
          if (attempts >= maxEvents * 3) break; // safety: stop after 3x failures
          attempts++;

          const targetUrl = ev.canonical_url || ev.url || "";
          if (!targetUrl) continue;
          const ek = ev.event_key || ev.id;
          const mp = "ticketmaster.com";

          // Rate-limit: 2s delay between Monid calls to avoid 502
          if (attempts > 1) await new Promise(r => setTimeout(r, 2000));

          try {
            const start = Date.now();
            const resp = await fetch(`${MONID_BASE}/v1/run`, {
              method: "POST",
              headers: { Authorization: `Bearer ${env.MONID_API_KEY}`, "Content-Type": "application/json" },
              body: JSON.stringify({ provider: "context.dev", endpoint: "/web/scrape/html", queryParams: { url: targetUrl } }),
            });
            const data: any = await resp.json();
            if (data.status !== "COMPLETED") continue;
            const html = data.output?.html || "";
            if (!html) continue;

            // Extract JSON-LD
            const extracted: Record<string, any> = {};
            const ldRegex = /<script[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi;
            let match;
            while ((match = ldRegex.exec(html)) !== null) {
              try {
                const ld = JSON.parse(match[1]);
                if (ld?.["@type"] === "Event" || ld?.["@type"] === "MusicEvent" || ld?.["@type"] === "Concert") {
                  const offers = ld.offers;
                  if (offers && !Array.isArray(offers)) {
                    extracted.price = offers.price;
                    extracted.currency = offers.priceCurrency;
                    extracted.availability = offers.availability;
                  }
                  extracted.name = ld.name;
                  extracted.venue = ld.location?.name;
                  extracted.city = ld.location?.address?.addressLocality;
                  break;
                }
              } catch {}
            }

            // SHA-256 for content-addressed storage
            const htmlBytes = new TextEncoder().encode(html);
            const hashBuf = await crypto.subtle.digest("SHA-256", htmlBytes.buffer as ArrayBuffer);
            const contentHash = [...new Uint8Array(hashBuf)].map(b => b.toString(16).padStart(2, "0")).join("");
            const h0 = contentHash.slice(0, 2), h1 = contentHash.slice(2, 4);

            // Write raw evidence to R2
            const rawKey = `raw/monid/${h0}/${h1}/${contentHash}.json`;
            await env.RAW_BUCKET.put(rawKey, JSON.stringify({ url: targetUrl, marketplace: mp, event_key: ek, provider: "context.dev", html: html.slice(0, 100_000), extracted, fetched_at: new Date().toISOString(), cost_usd: 0.0009 }), {
              httpMetadata: { contentType: "application/json" },
              customMetadata: { source: "monid", event_key: ek, content_hash: contentHash },
            });

            // Write normalized observation to R2 lake
            const now = new Date().toISOString();
            const observation = {
              schema_version: "ticket_market_snapshot_v1",
              event_key: ek, source_platform: mp, actor_or_endpoint: "monid_context.dev",
              observed_at: now, retrieved_at: now, knowledge_time: now,
              currency: extracted.currency || null, resale_min_price: extracted.price ?? null,
              sold_out_flag: String(extracted.availability || "").toLowerCase().includes("soldout"),
              identity_match_status: "MATCHED", source_url: targetUrl, raw_payload_hash: contentHash,
              rights_status: "TERMS_REVIEW_REQUIRED", commercial_use_status: "PROTOTYPE_ONLY",
            };
            const stagingKey = `staging/ticket_market/date=${now.slice(0, 10)}/hour=${now.slice(11, 13)}/${ek.replace(/[^a-zA-Z0-9]/g, "_")}.json`;
            await env.LAKE_BUCKET.put(stagingKey, JSON.stringify(observation, null, 2), {
              httpMetadata: { contentType: "application/json" },
            });

            totalCost += 0.0009;
            fetched++;
            results.push({
              event_key: ek, url: targetUrl, price: extracted.price ?? null,
              currency: extracted.currency || null, venue: extracted.venue || null,
              raw_key: rawKey, staging_key: stagingKey, cost_usd: 0.0009,
              latency_ms: Date.now() - start, content_hash: contentHash,
            });
          } catch (e: any) {
            results.push({ event_key: ek, error: e.message || String(e) });
          }
        }

        return Response.json({
          status: "PILOT_COMPLETE",
          events_fetched: fetched,
          total_cost_usd: totalCost,
          results,
        });
      } catch (e: any) {
        return Response.json({ error: e.message || String(e) }, { status: 500 });
      }
    }

    return Response.json({ error: "Not found" }, { status: 404 });
  },

  async queue(batch: MessageBatch<unknown>, env: Env): Promise<void> {
    const queueName = batch.queue;

    switch (queueName) {
      case "fi-acquisition-fast":
        await handleFastBatch(batch as MessageBatch<any>, env);
        break;
      case "fi-acquisition-deep":
        await handleDeepBatch(batch as MessageBatch<any>, env);
        break;
      case "fi-acquisition-processing":
        await handleProcessingBatch(batch as MessageBatch<any>, env);
        break;
      default:
        console.error(`Unknown queue: ${queueName}`);
        for (const msg of batch.messages) {
          msg.ack();
        }
    }
  },
};
