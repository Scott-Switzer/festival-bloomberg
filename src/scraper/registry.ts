/**
 * Festival source registry.
 * Defines the festivals to scrape and their configuration.
 */

export interface FestivalSource {
  id: string;
  name: string;
  url: string;
  year: number;
  parser: 'coachella' | 'bonnaroo' | 'lollapalooza' | 'glastonbury' | 'generic';
  active: boolean;
}

export const FESTIVAL_SOURCES: FestivalSource[] = [
  {
    id: 'coachella',
    name: 'Coachella Valley Music and Arts Festival',
    url: 'https://www.coachella.com',
    year: 2025,
    parser: 'coachella',
    active: true,
  },
  {
    id: 'bonnaroo',
    name: 'Bonnaroo Music and Arts Festival',
    url: 'https://www.bonnaroo.com',
    year: 2025,
    parser: 'bonnaroo',
    active: true,
  },
  {
    id: 'lollapalooza',
    name: 'Lollapalooza',
    url: 'https://www.lollapalooza.com',
    year: 2025,
    parser: 'lollapalooza',
    active: true,
  },
  {
    id: 'glastonbury',
    name: 'Glastonbury Festival',
    url: 'https://www.glastonburyfestivals.co.uk',
    year: 2025,
    parser: 'glastonbury',
    active: true,
  },
];

/**
 * Get active festival sources.
 */
export function getActiveSources(): FestivalSource[] {
  return FESTIVAL_SOURCES.filter(source => source.active);
}

/**
 * Get source by ID.
 */
export function getSourceById(id: string): FestivalSource | undefined {
  return FESTIVAL_SOURCES.find(source => source.id === id);
}
