/**
 * Canonical festival/source registry (Zod-validated) plus derived views:
 * SOURCE_REGISTRY, FESTIVAL_REGISTRY, EDITION_REGISTRY, VENUE_REGISTRY,
 * and FESTIVAL_SOURCES for the ingestion runner.
 *
 * Canonical edition year is 2026 (aligned with EDITION_REGISTRY / fixtures).
 */

import { z } from "zod";

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

/** Parser identity for lineup dispatch. Specialized kinds are reserved for
 * site-specific parsers; only kinds with a registered implementation are used. */
export const ParserKindSchema = z.enum([
  "coachella",
  "bonnaroo",
  "lollapalooza",
  "glastonbury",
  "generic",
]);
export type ParserKind = z.infer<typeof ParserKindSchema>;

const RefreshTtlSchema = z
  .object({
    softMs: z.number().positive(),
    hardMs: z.number().positive(),
  })
  .strict()
  .refine((ttl) => ttl.hardMs >= ttl.softMs, {
    message: "ttl.hardMs must be >= ttl.softMs",
  });

const VenueSchema = z
  .object({
    id: z.string().min(1),
    name: z.string().min(1),
    city: z.string().min(1).optional(),
    region: z.string().min(1).optional(),
    country: z.string().min(1).optional(),
    lat: z.number().finite().optional(),
    lon: z.number().finite().optional(),
  })
  .strict();

const EditionConfigSchema = z
  .object({
    id: z.string().min(1),
    year: z.number().int().gte(2000).lte(2100),
    startDate: z.string().optional(),
    endDate: z.string().optional(),
    venueIds: z.array(z.string().min(1)).min(1),
  })
  .strict();

const SourceConfigSchema = z
  .object({
    domain: z.string().min(1),
    priority: z.number().int(),
    ttl: RefreshTtlSchema,
    allowedPaths: z.array(z.string().min(1)).min(1),
    monidPreferred: z.boolean(),
    notes: z.string().optional(),
    /** Absolute lineup/home URL used by the ingestion runner. */
    url: z.string().url(),
    parser: ParserKindSchema,
    active: z.boolean(),
  })
  .strict();

const FestivalConfigSchema = z
  .object({
    id: z.string().min(1),
    name: z.string().min(1),
    /** Stable short id used by --sources / FESTIVAL_SOURCES. */
    slug: z.string().min(1),
    primaryDomain: z.string().min(1),
    editions: z.array(EditionConfigSchema).min(1),
    source: SourceConfigSchema,
  })
  .strict()
  .superRefine((fest, ctx) => {
    if (fest.source.domain !== fest.primaryDomain) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: `source.domain (${fest.source.domain}) must match primaryDomain (${fest.primaryDomain})`,
        path: ["source", "domain"],
      });
    }
    try {
      const host = new URL(fest.source.url).host;
      if (host !== fest.primaryDomain) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: `source.url host (${host}) must match primaryDomain (${fest.primaryDomain})`,
          path: ["source", "url"],
        });
      }
    } catch {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "source.url is not a valid URL",
        path: ["source", "url"],
      });
    }
  });

export const CanonicalRegistrySchema = z
  .object({
    venues: z.array(VenueSchema).min(1),
    festivals: z.array(FestivalConfigSchema).min(1),
  })
  .strict()
  .superRefine((reg, ctx) => {
    const venueIds = new Set(reg.venues.map((v) => v.id));
    if (venueIds.size !== reg.venues.length) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "duplicate venue id in venues[]",
        path: ["venues"],
      });
    }

    const festivalIds = new Set<string>();
    const slugs = new Set<string>();
    const domains = new Set<string>();
    const editionIds = new Set<string>();

    for (const [i, fest] of reg.festivals.entries()) {
      if (festivalIds.has(fest.id)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: `duplicate festival id ${fest.id}`,
          path: ["festivals", i, "id"],
        });
      }
      festivalIds.add(fest.id);

      if (slugs.has(fest.slug)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: `duplicate festival slug ${fest.slug}`,
          path: ["festivals", i, "slug"],
        });
      }
      slugs.add(fest.slug);

      if (domains.has(fest.primaryDomain)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: `duplicate primaryDomain ${fest.primaryDomain}`,
          path: ["festivals", i, "primaryDomain"],
        });
      }
      domains.add(fest.primaryDomain);

      for (const [j, edition] of fest.editions.entries()) {
        if (editionIds.has(edition.id)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: `duplicate edition id ${edition.id}`,
            path: ["festivals", i, "editions", j, "id"],
          });
        }
        editionIds.add(edition.id);

        for (const venueId of edition.venueIds) {
          if (!venueIds.has(venueId)) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              message: `unknown venueId ${venueId}`,
              path: ["festivals", i, "editions", j, "venueIds"],
            });
          }
        }
      }
    }
  });

export type CanonicalRegistry = z.infer<typeof CanonicalRegistrySchema>;
export type CanonicalFestivalConfig = CanonicalRegistry["festivals"][number];

/**
 * Single source of truth. Edition year is 2026 everywhere (tests/fixtures use 2026).
 * Parser kinds name intended specialized parsers; only "generic" is implemented today
 * — runner dispatch falls back to generic until specialized parsers are registered.
 */
const CANONICAL_REGISTRY_INPUT = {
  venues: [
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
  ],
  festivals: [
    {
      id: "fest_coachella",
      name: "Coachella Valley Music and Arts Festival",
      slug: "coachella",
      primaryDomain: "www.coachella.com",
      editions: [
        {
          id: "ed_coachella_2026",
          year: 2026,
          venueIds: ["venue_empire_polo"],
        },
      ],
      source: {
        domain: "www.coachella.com",
        priority: 100,
        ttl: DEFAULT_TTL,
        allowedPaths: ["/", "/lineup", "/passes", "/plan"],
        monidPreferred: true,
        url: "https://www.coachella.com",
        parser: "coachella" as const,
        active: true,
      },
    },
    {
      id: "fest_bonnaroo",
      name: "Bonnaroo Music and Arts Festival",
      slug: "bonnaroo",
      primaryDomain: "www.bonnaroo.com",
      editions: [
        {
          id: "ed_bonnaroo_2026",
          year: 2026,
          venueIds: ["venue_great_stage_park"],
        },
      ],
      source: {
        domain: "www.bonnaroo.com",
        priority: 90,
        ttl: DEFAULT_TTL,
        allowedPaths: ["/", "/lineup", "/tickets"],
        monidPreferred: true,
        url: "https://www.bonnaroo.com",
        parser: "bonnaroo" as const,
        active: true,
      },
    },
    {
      id: "fest_lolla",
      name: "Lollapalooza",
      slug: "lollapalooza",
      primaryDomain: "www.lollapalooza.com",
      editions: [
        {
          id: "ed_lolla_2026",
          year: 2026,
          venueIds: ["venue_grant_park"],
        },
      ],
      source: {
        domain: "www.lollapalooza.com",
        priority: 90,
        ttl: DEFAULT_TTL,
        allowedPaths: ["/", "/lineup", "/tickets"],
        monidPreferred: true,
        url: "https://www.lollapalooza.com",
        parser: "lollapalooza" as const,
        active: true,
      },
    },
    {
      id: "fest_glastonbury",
      name: "Glastonbury Festival",
      slug: "glastonbury",
      primaryDomain: "www.glastonburyfestivals.co.uk",
      editions: [
        {
          id: "ed_glastonbury_2026",
          year: 2026,
          venueIds: ["venue_worthy_farm"],
        },
      ],
      source: {
        domain: "www.glastonburyfestivals.co.uk",
        priority: 85,
        ttl: { softMs: 12 * 60 * 60 * 1000, hardMs: 48 * 60 * 60 * 1000 },
        allowedPaths: ["/", "/line-up", "/information"],
        monidPreferred: true,
        url: "https://www.glastonburyfestivals.co.uk",
        parser: "glastonbury" as const,
        active: true,
      },
    },
  ],
} as const;

export const CANONICAL_REGISTRY: CanonicalRegistry =
  CanonicalRegistrySchema.parse(CANONICAL_REGISTRY_INPUT);

function deriveSourceRegistry(reg: CanonicalRegistry): readonly SourceEntry[] {
  return reg.festivals.map((fest) => {
    const { domain, priority, ttl, allowedPaths, monidPreferred, notes } = fest.source;
    const entry: SourceEntry = {
      domain,
      priority,
      ttl,
      allowedPaths: [...allowedPaths],
      monidPreferred,
    };
    if (notes !== undefined) entry.notes = notes;
    return entry;
  });
}

function deriveVenueRegistry(reg: CanonicalRegistry): readonly Venue[] {
  return reg.venues.map((v) => ({ ...v }));
}

function deriveFestivalRegistry(reg: CanonicalRegistry): readonly Festival[] {
  return reg.festivals.map((fest) => ({
    id: fest.id,
    name: fest.name,
    slug: fest.slug,
    primaryDomain: fest.primaryDomain,
    editionIds: fest.editions.map((e) => e.id),
  }));
}

function deriveEditionRegistry(reg: CanonicalRegistry): readonly FestivalEdition[] {
  return reg.festivals.flatMap((fest) =>
    fest.editions.map((edition) => ({
      id: edition.id,
      festivalId: fest.id,
      year: edition.year,
      ...(edition.startDate !== undefined ? { startDate: edition.startDate } : {}),
      ...(edition.endDate !== undefined ? { endDate: edition.endDate } : {}),
      venueIds: [...edition.venueIds],
    })),
  );
}

/** Current (first) edition year for a festival — used by FESTIVAL_SOURCES. */
function currentEditionYear(fest: CanonicalFestivalConfig): number {
  return fest.editions[0]!.year;
}

function deriveFestivalSources(reg: CanonicalRegistry): FestivalSource[] {
  return reg.festivals.map((fest) => ({
    id: fest.slug,
    name: fest.name,
    url: fest.source.url,
    year: currentEditionYear(fest),
    parser: fest.source.parser,
    active: fest.source.active,
  }));
}

export const SOURCE_REGISTRY: readonly SourceEntry[] =
  deriveSourceRegistry(CANONICAL_REGISTRY);

export const VENUE_REGISTRY: readonly Venue[] =
  deriveVenueRegistry(CANONICAL_REGISTRY);

export const FESTIVAL_REGISTRY: readonly Festival[] =
  deriveFestivalRegistry(CANONICAL_REGISTRY);

export const EDITION_REGISTRY: readonly FestivalEdition[] =
  deriveEditionRegistry(CANONICAL_REGISTRY);

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

// ---------------------------------------------------------------------------
// Ingestion runner sources (URLs + parser hints) — derived from canonical
// ---------------------------------------------------------------------------

export interface FestivalSource {
  id: string;
  name: string;
  url: string;
  year: number;
  parser: ParserKind;
  active: boolean;
}

export const FESTIVAL_SOURCES: FestivalSource[] =
  deriveFestivalSources(CANONICAL_REGISTRY);

export function getActiveSources(): FestivalSource[] {
  return FESTIVAL_SOURCES.filter((source) => source.active);
}

export function getSourceById(id: string): FestivalSource | undefined {
  return FESTIVAL_SOURCES.find((source) => source.id === id);
}

export class UnknownSourceError extends Error {
  readonly unknownIds: string[];
  readonly registeredIds: string[];

  constructor(unknownIds: string[], registeredIds: string[]) {
    const unknown = unknownIds.join(", ");
    const registered = registeredIds.join(", ");
    super(
      `Unknown source ID(s): ${unknown}. Registered sources: ${registered || "(none)"}`,
    );
    this.name = "UnknownSourceError";
    this.unknownIds = unknownIds;
    this.registeredIds = registeredIds;
  }
}

/**
 * Resolve --sources IDs against the registry. Throws UnknownSourceError when
 * any ID is unregistered (no synthetic generic fallbacks).
 */
export function resolveSourcesByIds(ids: string[]): FestivalSource[] {
  if (ids.length === 0) return getActiveSources();

  const registeredIds = FESTIVAL_SOURCES.map((s) => s.id);
  const unknownIds = [...new Set(ids)].filter((id) => !getSourceById(id));
  if (unknownIds.length > 0) {
    throw new UnknownSourceError(unknownIds, registeredIds);
  }

  return ids.map((id) => getSourceById(id)!);
}

/** Assert derived registries stay consistent with the canonical config. */
export function assertRegistryConsistency(): void {
  CanonicalRegistrySchema.parse(CANONICAL_REGISTRY);

  const years = new Set(EDITION_REGISTRY.map((e) => e.year));
  const sourceYears = new Set(FESTIVAL_SOURCES.map((s) => s.year));
  if (years.size !== 1 || sourceYears.size !== 1 || [...years][0] !== [...sourceYears][0]) {
    throw new Error(
      `Registry year inconsistency: editions=${[...years].join(",")} sources=${[...sourceYears].join(",")}`,
    );
  }

  for (const fest of FESTIVAL_REGISTRY) {
    for (const editionId of fest.editionIds) {
      const edition = getEdition(editionId);
      if (!edition || edition.festivalId !== fest.id) {
        throw new Error(`Festival ${fest.id} references missing edition ${editionId}`);
      }
    }
    if (fest.primaryDomain && !getSource(fest.primaryDomain)) {
      throw new Error(`Festival ${fest.id} primaryDomain not in SOURCE_REGISTRY`);
    }
    const source = FESTIVAL_SOURCES.find((s) => s.id === fest.slug);
    if (!source) {
      throw new Error(`Festival slug ${fest.slug} missing from FESTIVAL_SOURCES`);
    }
  }

  for (const edition of EDITION_REGISTRY) {
    for (const venueId of edition.venueIds) {
      if (!getVenue(venueId)) {
        throw new Error(`Edition ${edition.id} references unknown venue ${venueId}`);
      }
    }
  }
}
