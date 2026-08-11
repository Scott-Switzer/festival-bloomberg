'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { Activity, Calendar, TrendingUp, DollarSign, MapPin, Settings } from 'lucide-react'

export default function Home() {
  const [activeView, setActiveView] = useState('overview')
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<any[]>([])
  const [showResults, setShowResults] = useState(false)

  const handleSearch = async (query: string) => {
    if (!query.trim()) {
      setSearchResults([])
      setShowResults(false)
      return
    }
    
    try {
      const response = await fetch('http://localhost:8000/artists/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query })
      })
      const results = await response.json()
      setSearchResults(results)
      setShowResults(true)
    } catch (err) {
      console.error('Search failed:', err)
      setSearchResults([])
    }
  }

  const handleArtistClick = async (artistId: string, artistName: string) => {
    setActiveView('artist')
    try {
      const response = await fetch(`http://localhost:8000/artists/${artistId}`)
      const artistData = await response.json()
      console.log('Artist data:', artistData)
    } catch (err) {
      console.error('Failed to fetch artist data:', err)
    }
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch(searchQuery)
    }
  }

  return (
    <div className="min-h-screen bg-terminal-bg text-terminal-fg">
      {/* Header */}
      <header className="border-b border-terminal-muted p-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-terminal-primary">FESTIVAL INTELLIGENCE TERMINAL</h1>
            <p className="text-xs text-terminal-muted">Decision-support platform for festival talent buyers</p>
          </div>
          <div className="text-xs text-terminal-muted">
            {new Date().toLocaleDateString()}
          </div>
        </div>
      </header>

      <div className="flex">
        {/* Sidebar */}
        <aside className="w-64 border-r border-terminal-muted p-4">
          <nav className="space-y-2">
            <button
              onClick={() => setActiveView('overview')}
              className={`w-full text-left px-3 py-2 rounded text-sm ${activeView === 'overview' ? 'bg-terminal-primary text-terminal-bg font-bold' : 'text-terminal-muted hover:text-terminal-primary'}`}
            >
              <span className="flex items-center gap-2">
                <Activity size={16} />
                Market Overview
              </span>
            </button>
            <button
              onClick={() => setActiveView('artist')}
              className={`w-full text-left px-3 py-2 rounded text-sm ${activeView === 'artist' ? 'bg-terminal-primary text-terminal-bg font-bold' : 'text-terminal-muted hover:text-terminal-primary'}`}
            >
              <span className="flex items-center gap-2">
                <TrendingUp size={16} />
                Artist Terminal
              </span>
            </button>
            <button
              onClick={() => setActiveView('festival')}
              className={`w-full text-left px-3 py-2 rounded text-sm ${activeView === 'festival' ? 'bg-terminal-primary text-terminal-bg font-bold' : 'text-terminal-muted hover:text-terminal-primary'}`}
            >
              <span className="flex items-center gap-2">
                <Calendar size={16} />
                Festival Comparison
              </span>
            </button>
            <button
              onClick={() => setActiveView('revenue')}
              className={`w-full text-left px-3 py-2 rounded text-sm ${activeView === 'revenue' ? 'bg-terminal-primary text-terminal-bg font-bold' : 'text-terminal-muted hover:text-terminal-primary'}`}
            >
              <span className="flex items-center gap-2">
                <DollarSign size={16} />
                Revenue Scenarios
              </span>
            </button>
            <button
              onClick={() => setActiveView('location')}
              className={`w-full text-left px-3 py-2 rounded text-sm ${activeView === 'location' ? 'bg-terminal-primary text-terminal-bg font-bold' : 'text-terminal-muted hover:text-terminal-primary'}`}
            >
              <span className="flex items-center gap-2">
                <MapPin size={16} />
                Location Intelligence
              </span>
            </button>
            <button
              onClick={() => setActiveView('settings')}
              className={`w-full text-left px-3 py-2 rounded text-sm ${activeView === 'settings' ? 'bg-terminal-primary text-terminal-bg font-bold' : 'text-terminal-muted hover:text-terminal-primary'}`}
            >
              <span className="flex items-center gap-2">
                <Settings size={16} />
                Settings
              </span>
            </button>
          </nav>

          <div className="mt-8 p-4 border border-terminal-muted rounded">
            <h3 className="text-sm font-bold text-terminal-secondary mb-2">QUICK SEARCH</h3>
            <input
              type="text"
              placeholder="Search artists or festivals..."
              className="w-full bg-terminal-bg border border-terminal-muted rounded px-3 py-2 text-sm focus:outline-none focus:border-terminal-primary"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  handleSearch(searchQuery)
                }
              }}
            />
            <button
              onClick={() => handleSearch(searchQuery)}
              className="mt-2 w-full px-4 py-2 bg-terminal-primary text-terminal-bg rounded font-bold text-sm"
            >
              Search
            </button>
            {showResults && searchResults.length > 0 && (
              <div className="mt-2 space-y-1 max-h-48 overflow-y-auto">
                {searchResults.map((result: any) => (
                  <button
                    key={result.id || result.musicbrainz_id}
                    onClick={() => handleArtistClick(result.id || result.musicbrainz_id, result.name)}
                    className="w-full text-left px-3 py-2 rounded text-sm text-terminal-muted hover:text-terminal-primary hover:bg-terminal-muted transition-colors"
                  >
                    <div className="font-medium">{result.name}</div>
                    {result.genres && (
                      <div className="text-xs text-terminal-muted mt-1">
                        {Array.isArray(result.genres) ? result.genres.join(', ') : result.genres}
                      </div>
                    )}
                    {result.momentum_score && (
                      <div className="text-xs text-terminal-primary mt-1">
                        Momentum: {result.momentum_score.toFixed(1)}
                      </div>
                    )}
                  </button>
                ))}
              </div>
            )}
            {showResults && searchResults.length === 0 && (
              <div className="mt-2 text-xs text-terminal-muted">
                No results found
              </div>
            )}
          </div>
        </aside>

        {/* Main Content */}
        <main className="flex-1 p-6">
          {activeView === 'overview' && <MarketOverview />}
          {activeView === 'artist' && <ArtistTerminal />}
          {activeView === 'festival' && <FestivalComparison />}
          {activeView === 'revenue' && <RevenueScenarios />}
          {activeView === 'location' && <LocationIntelligence />}
          {activeView === 'settings' && <SettingsView />}
        </main>
      </div>
    </div>
  )
}

function MarketOverview() {
  const [marketData, setMarketData] = useState<any>({
    top_momentum_artists: [
      { artist_id: '66CXWjxzNUsdJxJ2JdwvnR', name: 'Ariana Grande', momentum_score: 93.0, change_30d: 3 },
      { artist_id: '26VFTg2z8YR0cCuwLzESi2', name: 'Halsey', momentum_score: 95.0, change_30d: -10 },
      { artist_id: '0Y5tJX1MQlPlqiwlOH1tJY', name: 'Travis Scott', momentum_score: 91.0, change_30d: 1 },
      { artist_id: '246dkjvS1zLTtiykXe5h60', name: 'Post Malone', momentum_score: 96.0, change_30d: 1 }
    ],
    upcoming_festivals: [
      { festival_id: 'coachella', name: 'Coachella', date: '2025-04-11' },
      { festival_id: 'bonnaroo', name: 'Bonnaroo', date: '2025-06-12' }
    ]
  })
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await fetch('http://localhost:8000/market/overview')
        const data = await response.json()
        setMarketData(data)
      } catch (err) {
        console.error('Fetch error:', err)
      }
    }
    fetchData()
  }, [])

  const artists = marketData?.top_momentum_artists || []
  const festivals = marketData?.upcoming_festivals || []

  return (
    <div className="space-y-6">
      <div className="border-b border-terminal-muted pb-4">
        <h2 className="text-2xl font-bold text-terminal-primary">MARKET OVERVIEW</h2>
        <p className="text-terminal-muted text-sm mt-1">
          Real-time intelligence on artist momentum, festival announcements, and demand shifts
        </p>
      </div>

      {/* Key Metrics */}
      <div className="grid grid-cols-4 gap-4">
        <MetricCard
          title="Top Momentum Artists"
          value={artists.length}
          change={`+${artists[0]?.change_30d?.toFixed(1) || 0}%`}
          positive
        />
        <MetricCard
          title="Upcoming Festivals"
          value={festivals.length}
          change="+2"
          positive
        />
        <MetricCard
          title="Demand Shifts"
          value="Active"
          change="Pop +5.2%"
          positive
        />
        <MetricCard
          title="Weather Risks"
          value="2"
          change="Moderate"
          warning
        />
      </div>

      {/* Top Artists Table */}
      <div className="border border-terminal-muted rounded">
        <div className="p-4 border-b border-terminal-muted">
          <h3 className="font-bold text-terminal-secondary">TOP MOMENTUM ARTISTS</h3>
        </div>
        <table className="w-full">
          <thead>
            <tr className="text-terminal-muted text-sm border-b border-terminal-muted">
              <th className="text-left p-3">ARTIST</th>
              <th className="text-right p-3">MOMENTUM</th>
              <th className="text-right p-3">30D CHANGE</th>
              <th className="text-right p-3">BVI</th>
            </tr>
          </thead>
          <tbody>
            {artists.map((artist: any) => (
              <TableRow 
                key={artist.artist_id}
                artist={artist.name} 
                momentum={artist.momentum_score} 
                change={artist.change_30d} 
                bvi={artist.momentum_score * 0.9} 
              />
            ))}
          </tbody>
        </table>
      </div>

      {/* Upcoming Festivals */}
      <div className="border border-terminal-muted rounded">
        <div className="p-4 border-b border-terminal-muted">
          <h3 className="font-bold text-terminal-secondary">UPCOMING FESTIVALS</h3>
        </div>
        <div className="p-4 space-y-3">
          {festivals.map((festival: any) => (
            <FestivalRow 
              key={festival.festival_id}
              name={festival.name} 
              location="TBD" 
              date={festival.date} 
            />
          ))}
        </div>
      </div>
    </div>
  )
}

function ArtistTerminal() {
  const [selectedArtist, setSelectedArtist] = useState<any>(null)

  return (
    <div className="space-y-6">
      <div className="border-b border-terminal-muted pb-4">
        <h2 className="text-2xl font-bold text-terminal-primary">ARTIST TERMINAL</h2>
        <p className="text-terminal-muted text-sm mt-1">
          Deep dive into artist intelligence, momentum, and booking value
        </p>
      </div>

      {!selectedArtist ? (
        <div className="p-8 border border-terminal-muted rounded text-center text-terminal-muted">
          <p>Select an artist to view detailed intelligence</p>
          <button 
            onClick={() => setSelectedArtist({
              name: 'Ariana Grande',
              momentum_score: 93.0,
              booking_value: 88.2,
              tour_probability: 0.75,
              genre: 'Pop',
              followers: '34.5M'
            })}
            className="mt-4 px-4 py-2 bg-terminal-primary text-terminal-bg rounded font-bold"
          >
            View Sample Artist
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="border border-terminal-muted rounded p-4">
            <h3 className="text-xl font-bold text-terminal-primary">{selectedArtist.name}</h3>
            <div className="grid grid-cols-4 gap-4 mt-4">
              <div>
                <div className="text-terminal-muted text-sm">Momentum Score</div>
                <div className="text-2xl font-bold text-terminal-primary">{selectedArtist.momentum_score.toFixed(1)}</div>
              </div>
              <div>
                <div className="text-terminal-muted text-sm">Booking Value</div>
                <div className="text-2xl font-bold text-terminal-secondary">{selectedArtist.booking_value.toFixed(1)}</div>
              </div>
              <div>
                <div className="text-terminal-muted text-sm">Tour Probability</div>
                <div className="text-2xl font-bold text-terminal-accent">{(selectedArtist.tour_probability * 100).toFixed(0)}%</div>
              </div>
              <div>
                <div className="text-terminal-muted text-sm">Followers</div>
                <div className="text-2xl font-bold">{selectedArtist.followers}</div>
              </div>
            </div>
          </div>
          <button 
            onClick={() => setSelectedArtist(null)}
            className="px-4 py-2 bg-terminal-muted text-terminal-fg rounded"
          >
            Back to Search
          </button>
        </div>
      )}
    </div>
  )
}

function FestivalComparison() {
  return (
    <div className="space-y-6">
      <div className="border-b border-terminal-muted pb-4">
        <h2 className="text-2xl font-bold text-terminal-primary">FESTIVAL COMPARISON</h2>
        <p className="text-terminal-muted text-sm mt-1">
          Compare lineups, genre diversity, and competitive positioning
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="border border-terminal-muted rounded p-4">
          <h3 className="font-bold text-terminal-primary mb-2">Coachella</h3>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-terminal-muted">Lineup Strength</span>
              <span className="text-terminal-primary">85.2</span>
            </div>
            <div className="flex justify-between">
              <span className="text-terminal-muted">Genre Diversity</span>
              <span className="text-terminal-primary">0.82</span>
            </div>
            <div className="flex justify-between">
              <span className="text-terminal-muted">Headliner Dependency</span>
              <span className="text-terminal-accent">0.18</span>
            </div>
          </div>
        </div>
        <div className="border border-terminal-muted rounded p-4">
          <h3 className="font-bold text-terminal-primary mb-2">Bonnaroo</h3>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-terminal-muted">Lineup Strength</span>
              <span className="text-terminal-primary">78.5</span>
            </div>
            <div className="flex justify-between">
              <span className="text-terminal-muted">Genre Diversity</span>
              <span className="text-terminal-primary">0.75</span>
            </div>
            <div className="flex justify-between">
              <span className="text-terminal-muted">Headliner Dependency</span>
              <span className="text-terminal-accent">0.25</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function RevenueScenarios() {
  return (
    <div className="space-y-6">
      <div className="border-b border-terminal-muted pb-4">
        <h2 className="text-2xl font-bold text-terminal-primary">REVENUE SCENARIOS</h2>
        <p className="text-terminal-muted text-sm mt-1">
          Monte Carlo forecasting for festival revenue under uncertainty
        </p>
      </div>

      <div className="border border-terminal-muted rounded p-4">
        <h3 className="font-bold text-terminal-primary mb-4">Sample Scenario: Coachella 2025</h3>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <div className="text-terminal-muted text-sm">P10 Downside</div>
            <div className="text-xl font-bold text-terminal-accent">$8.2M</div>
          </div>
          <div>
            <div className="text-terminal-muted text-sm">P50 Base Case</div>
            <div className="text-xl font-bold text-terminal-primary">$12.5M</div>
          </div>
          <div>
            <div className="text-terminal-muted text-sm">P90 Upside</div>
            <div className="text-xl font-bold text-terminal-secondary">$16.8M</div>
          </div>
        </div>
        <div className="mt-4 pt-4 border-t border-terminal-muted">
          <div className="flex justify-between text-sm">
            <span className="text-terminal-muted">Break-even Attendance</span>
            <span>45,000</span>
          </div>
          <div className="flex justify-between text-sm mt-2">
            <span className="text-terminal-muted">Profitability Probability</span>
            <span className="text-terminal-primary">78%</span>
          </div>
        </div>
      </div>
    </div>
  )
}

function LocationIntelligence() {
  return (
    <div className="space-y-6">
      <div className="border-b border-terminal-muted pb-4">
        <h2 className="text-2xl font-bold text-terminal-primary">LOCATION INTELLIGENCE</h2>
        <p className="text-terminal-muted text-sm mt-1">
          Weather risk, air access, hotel pressure, and market demographics
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="border border-terminal-muted rounded p-4">
          <h3 className="font-bold text-terminal-primary mb-2">Indio, CA (Coachella)</h3>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-terminal-muted">Weather Risk</span>
              <span className="text-terminal-warning">Moderate</span>
            </div>
            <div className="flex justify-between">
              <span className="text-terminal-muted">Air Access</span>
              <span className="text-terminal-primary">78.5</span>
            </div>
            <div className="flex justify-between">
              <span className="text-terminal-muted">Hotel Pressure</span>
              <span className="text-terminal-accent">High</span>
            </div>
          </div>
        </div>
        <div className="border border-terminal-muted rounded p-4">
          <h3 className="font-bold text-terminal-primary mb-2">Manchester, TN (Bonnaroo)</h3>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-terminal-muted">Weather Risk</span>
              <span className="text-terminal-accent">High</span>
            </div>
            <div className="flex justify-between">
              <span className="text-terminal-muted">Air Access</span>
              <span className="text-terminal-primary">65.0</span>
            </div>
            <div className="flex justify-between">
              <span className="text-terminal-muted">Hotel Pressure</span>
              <span className="text-terminal-primary">Moderate</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function SettingsView() {
  return (
    <div className="space-y-6">
      <div className="border-b border-terminal-muted pb-4">
        <h2 className="text-2xl font-bold text-terminal-primary">SETTINGS</h2>
      </div>

      <div className="border border-terminal-muted rounded p-4">
        <h3 className="font-bold text-terminal-primary mb-4">API Configuration</h3>
        <div className="space-y-3 text-sm">
          <div className="flex justify-between">
            <span className="text-terminal-muted">MusicBrainz</span>
            <span className="text-terminal-primary">Connected</span>
          </div>
          <div className="flex justify-between">
            <span className="text-terminal-muted">setlist.fm</span>
            <span className="text-terminal-accent">Not Configured</span>
          </div>
          <div className="flex justify-between">
            <span className="text-terminal-muted">Ticketmaster</span>
            <span className="text-terminal-primary">Connected</span>
          </div>
          <div className="flex justify-between">
            <span className="text-terminal-muted">YouTube</span>
            <span className="text-terminal-accent">Not Configured</span>
          </div>
        </div>
      </div>

      <div className="border border-terminal-muted rounded p-4">
        <h3 className="font-bold text-terminal-primary mb-4">Data Source</h3>
        <div className="text-sm">
          <div className="text-terminal-muted">Current Dataset</div>
          <div className="text-terminal-primary">MusicOSet (11,518 artists)</div>
        </div>
      </div>
    </div>
  )
}

function MetricCard({ title, value, change, positive, warning }: any) {
  return (
    <div className="border border-terminal-muted rounded p-4">
      <div className="text-terminal-muted text-sm">{title}</div>
      <div className="text-2xl font-bold mt-2">{value}</div>
      <div className={`text-sm mt-1 ${positive ? 'text-terminal-primary' : warning ? 'text-terminal-warning' : 'text-terminal-accent'}`}>
        {change}
      </div>
    </div>
  )
}

function TableRow({ artist, momentum, change, bvi }: any) {
  return (
    <tr className="border-b border-terminal-muted hover:bg-terminal-muted/10">
      <td className="p-3">{artist}</td>
      <td className="text-right p-3 text-terminal-primary">{momentum.toFixed(1)}</td>
      <td className={`text-right p-3 ${change > 0 ? 'text-terminal-primary' : 'text-terminal-accent'}`}>
        {change > 0 ? '+' : ''}{change.toFixed(1)}%
      </td>
      <td className="text-right p-3 text-terminal-secondary">{bvi.toFixed(1)}</td>
    </tr>
  )
}

function FestivalRow({ name, date, location }: any) {
  return (
    <div className="flex items-center justify-between p-3 border border-terminal-muted rounded hover:bg-terminal-muted/10">
      <div>
        <div className="font-bold">{name}</div>
        <div className="text-sm text-terminal-muted">{location}</div>
      </div>
      <div className="text-terminal-secondary">{date}</div>
    </div>
  )
}
