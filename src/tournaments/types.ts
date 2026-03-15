export interface TournamentEntry {
  skillName: string;
  version: number;
  instructions: string;
  wins: number;
  losses: number;
  draws: number;
  elo: number;
  avgQualityScore: number;
  createdAt: string;
}

export interface MatchResult {
  tournamentId: string;
  challenge: string;
  entryA: string;
  entryB: string;
  outputA: string;
  outputB: string;
  winner: 'A' | 'B' | 'draw';
  scoreA: number;
  scoreB: number;
  judgeReasoning: string;
  timestamp: string;
}

export interface Tournament {
  id: string;
  category: string;
  entries: TournamentEntry[];
  matches: MatchResult[];
  status: 'active' | 'completed';
  createdAt: string;
  completedAt?: string;
}

export interface TournamentConfig {
  minEntriesForTournament: number;
  matchesPerRound: number;
  promotionThreshold: number;
  retirementThreshold: number;
}
