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
