import { describe, it, expect } from "vitest";
import {
  fingerprintFor,
  extractProviderToken,
  preferredRepresentations,
  MARKETPLACE_FINGERPRINTS,
} from "../src/fingerprint";

describe("marketplace fingerprint library", () => {
  it("covers all eight target marketplaces + dice/eventbrite", () => {
    const mps = MARKETPLACE_FINGERPRINTS.map((f) => f.marketplace);
    for (const mp of [
      "ticketmaster.com",
      "ticketweb.com",
      "axs.com",
      "seatgeek.com",
      "stubhub.com",
      "vividseats.com",
      "tickpick.com",
      "gametime.com",
      "dice.fm",
      "eventbrite.com",
    ]) {
      expect(mps).toContain(mp);
    }
  });

  it("fingerprintFor resolves by exact marketplace and by host pattern", () => {
    expect(fingerprintFor("ticketmaster.com")?.marketplace).toBe("ticketmaster.com");
    expect(fingerprintFor("www.gametime.co")?.marketplace).toBe("gametime.com");
    // Unknown domains fail closed (no invented marketplace identity).
    expect(fingerprintFor("unknown.example")).toBeNull();
  });

  it("every fingerprint declares structured representations to try", () => {
    for (const f of MARKETPLACE_FINGERPRINTS) {
      expect(f.representations.length).toBeGreaterThan(0);
      expect(f.identity_evidence.length).toBeGreaterThan(0);
      expect(f.rights_notes.length).toBeGreaterThan(0);
    }
  });

  it("preferredRepresentations prefers structured over unknown", () => {
    expect(preferredRepresentations("ticketmaster.com")).toContain("JSON_LD");
    expect(preferredRepresentations("stubhub.com")).toContain("NEXT_DATA");
    expect(preferredRepresentations("totally.unknown")).toEqual(["UNKNOWN"]);
  });

  it("extractProviderToken pulls the event id from ticketmaster URLs", () => {
    const token = extractProviderToken(
      "https://www.ticketmaster.com/hazlett-chicago-illinois-10-04-2026/event/040064CADD7D1593",
      "ticketmaster.com"
    );
    expect(token).toBe("040064CADD7D1593");
  });

  it("extractProviderToken pulls the numeric id from ticketweb URLs", () => {
    const token = extractProviderToken(
      "https://www.ticketweb.com/event/weatherday-bottom-lounge-tickets/14839013",
      "ticketweb.com"
    );
    expect(token).toBe("14839013");
  });

  it("extractProviderToken returns null when no token is present", () => {
    expect(extractProviderToken("https://ticketmaster.com/", "ticketmaster.com")).toBeNull();
  });
});
