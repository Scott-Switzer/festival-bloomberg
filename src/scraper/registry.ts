/**
 * Source registry (domains, TTLs, priorities, allowed paths) and
 * target registry (festivals, editions, venues).
 */

export type RefreshTtl = {
  /** Soft TTL: prefer refresh after this many ms when convenient. */
  softMs: number;
  /** Hard TTL: must refresh after this many ms. */
  hardMs: number;
};

export type SourceEntry = {
  domain: string;
  priority: number;
  ttl: RefreshTtl;
  allowedPaths: string[];
  /** Higher = prefer for structured managed fetch via Monid. */
  monidPreferred: boolean;
  notes?: string;
};

export type Venue = {
  id: string;
  name: string;
  city?: string;
  region?: string;
  country?: string;
  lat?: number;
  lon?: number;
};

export type FestivalEdition = {
  id: string;
  festivalId: string;
  year: number;
  startDate?: string;
  endDate?: string;
  venueIds: string[];
};

export type Festival = {
  id: string;
  name: string;
  slug: string;
  primaryDomain?: string;
  editionIds: string[];
};

/** Default soft 6h / hard 24h unless overridden. */
export const DEFAULT_TTL: RefreshTtl = {
  softMs: 6 * 60 * 60 * 1000,
  hardMs: 24 * 60 * 60 * 1000,
};

export const SOURCE_REGISTRY: readonly SourceEntry[] = [
  {
    domain: "www.coachella.com",
    priority: 100,
    ttl: DEFAULT_TTL,
    allowedPaths: ["/", "/lineup", "/passes", "/plan"],
    monidPreferred: true,
  },
  {
    domain: "www.bonnaroo.com",
    priority: 90,
    ttl: DEFAULT_TTL,
    allowedPaths: ["/", "/lineup", "/tickets"],
    monidPreferred: true,
  },
  {
    domain: "www.lollapalooza.com",
    priority: 90,
    ttl: DEFAULT_TTL,
    allowedPaths: ["/", "/lineup", "/tickets"],
    monidPreferred: true,
  },
  {
    domain: "www.glastonburyfestivals.co.uk",
    priority: 85,
    ttl: { softMs: 12 * 60 * 60 * 1000, hardMs: 48 * 60 * 60 * 1000 },
    allowedPaths: ["/", "/line-up", "/information"],
    monidPreferred: true,
  },
] as const;

export const VENUE_REGISTRY: readonly Venue[] = [
  {
    id: "venue_empire_polo",
    name: "Empire Polo Club",
    city: "Indio",
    region: "CA",
    country: "US",
  },
  {
    id: "venue_great_stage_park",
    name: "Great Stage Park",
    city: "Manchester",
    region: "TN",
    country: "US",
  },
  {
    id: "venue_grant_park",
    name: "Grant Park",
    city: "Chicago",
    region: "IL",
    country: "US",
  },
  {
    id: "venue_worthy_farm",
    name: "Worthy Farm",
    city: "Pilton",
    region: "Somerset",
    country: "GB",
  },
] as const;

export const FESTIVAL_REGISTRY: readonly Festival[] = [
  {
    id: "fest_coachella",
    name: "Coachella Valley Music and Arts Festival",
    slug: "coachella",
    primaryDomain: "www.coachella.com",
    editionIds: ["ed_coachella_2026"],
  },
  {
    id: "fest_bonnaroo",
    name: "Bonnaroo Music and Arts Festival",
    slug: "bonnaroo",
    primaryDomain: "www.bonnaroo.com",
    editionIds: ["ed_bonnaroo_2026"],
  },
  {
    id: "fest_lolla",
    name: "Lollapalooza",
    slug: "lollapalooza",
    primaryDomain: "www.lollapalooza.com",
    editionIds: ["ed_lolla_2026"],
  },
  {
    id: "fest_glastonbury",
    name: "Glastonbury Festival",
    slug: "glastonbury",
    primaryDomain: "www.glastonburyfestivals.co.uk",
    editionIds: ["ed_glastonbury_2026"],
  },
] as const;

export const EDITION_REGISTRY: readonly FestivalEdition[] = [
  {
    id: "ed_coachella_2026",
    festivalId: "fest_coachella",
    year: 2026,
    venueIds: ["venue_empire_polo"],
  },
  {
    id: "ed_bonnaroo_2026",
    festivalId: "fest_bonnaroo",
    year: 2026,
    venueIds: ["venue_great_stage_park"],
  },
  {
    id: "ed_lolla_2026",
    festivalId: "fest_lolla",
    year: 2026,
    venueIds: ["venue_grant_park"],
  },
  {
    id: "ed_glastonbury_2026",
    festivalId: "fest_glastonbury",
    year: 2026,
    venueIds: ["venue_worthy_farm"],
  },
] as const;

export function getSource(domain: string): SourceEntry | undefined {
  return SOURCE_REGISTRY.find((s) => s.domain === domain);
}

export function isPathAllowed(domain: string, pathname: string): boolean {
  const source = getSource(domain);
  if (!source) return false;
  const path = pathname.split("?")[0] || "/";
  return source.allowedPaths.some((allowed) => {
    if (allowed === "/") return path === "/";
    return path === allowed || path.startsWith(`${allowed}/`);
  });
}

export function sourcesByPriority(): SourceEntry[] {
  return [...SOURCE_REGISTRY].sort((a, b) => b.priority - a.priority);
}

export function getFestival(id: string): Festival | undefined {
  return FESTIVAL_REGISTRY.find((f) => f.id === id);
}

export function getEdition(id: string): FestivalEdition | undefined {
  return EDITION_REGISTRY.find((e) => e.id === id);
}

export function getVenue(id: string): Venue | undefined {
  return VENUE_REGISTRY.find((v) => v.id === id);
}

export function editionsForFestival(festivalId: string): FestivalEdition[] {
  return EDITION_REGISTRY.filter((e) => e.festivalId === festivalId);
}
