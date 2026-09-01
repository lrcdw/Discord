PRAGMA foreign_keys = ON;

-- Step E: versioned TEAM_MASTER v1.0
CREATE TABLE IF NOT EXISTS team_master_versions (
  team_master_version TEXT PRIMARY KEY,
  created_at_utc TEXT NOT NULL,
  parent_version TEXT,
  code_version TEXT,
  status TEXT NOT NULL,
  immutable INTEGER NOT NULL DEFAULT 1 CHECK (immutable IN (0,1)),
  team_rows INTEGER NOT NULL DEFAULT 0 CHECK (team_rows >= 0),
  alias_rows INTEGER NOT NULL DEFAULT 0 CHECK (alias_rows >= 0),
  provider_id_rows INTEGER NOT NULL DEFAULT 0 CHECK (provider_id_rows >= 0),
  notes TEXT
);

CREATE TABLE IF NOT EXISTS team_master (
  team_master_version TEXT NOT NULL,
  team_id TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  canonical_name_normalized TEXT NOT NULL,
  country TEXT NOT NULL,
  competition TEXT,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  entity_status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_at_utc TEXT NOT NULL,
  notes TEXT,
  PRIMARY KEY (team_master_version, team_id, valid_from),
  FOREIGN KEY (team_master_version) REFERENCES team_master_versions(team_master_version),
  CHECK (entity_status IN ('ACTIVE','INACTIVE','MERGED','DISSOLVED','UNKNOWN')),
  CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS team_aliases (
  team_master_version TEXT NOT NULL,
  alias_id TEXT NOT NULL,
  team_id TEXT NOT NULL,
  alias TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  source_id TEXT,
  competition TEXT,
  country TEXT,
  alias_type TEXT NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  evidence_ref TEXT,
  created_at_utc TEXT NOT NULL,
  PRIMARY KEY (team_master_version, alias_id),
  FOREIGN KEY (team_master_version) REFERENCES team_master_versions(team_master_version),
  CHECK (alias_type IN ('PROVIDER_NAME','HISTORICAL_NAME','SHORT_NAME','ACRONYM','OTHER')),
  CHECK (verification_status IN ('VERIFIED','UNVERIFIED','REJECTED')),
  CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS team_source_ids (
  team_master_version TEXT NOT NULL,
  mapping_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  provider_team_id TEXT NOT NULL,
  team_id TEXT NOT NULL,
  provider_name_observed TEXT,
  verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  evidence_ref TEXT,
  first_collection_time TEXT,
  last_collection_time TEXT,
  PRIMARY KEY (team_master_version, mapping_id),
  FOREIGN KEY (team_master_version) REFERENCES team_master_versions(team_master_version),
  CHECK (verification_status IN ('VERIFIED','UNVERIFIED','REJECTED')),
  CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS team_competition_membership (
  team_master_version TEXT NOT NULL,
  membership_id TEXT NOT NULL,
  team_id TEXT NOT NULL,
  competition TEXT NOT NULL,
  season TEXT,
  country TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  source_id TEXT,
  verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
  evidence_ref TEXT,
  PRIMARY KEY (team_master_version, membership_id),
  FOREIGN KEY (team_master_version) REFERENCES team_master_versions(team_master_version),
  CHECK (verification_status IN ('VERIFIED','UNVERIFIED','REJECTED')),
  CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS team_resolution_log (
  resolution_id TEXT PRIMARY KEY,
  resolved_at_utc TEXT NOT NULL,
  team_master_version TEXT NOT NULL,
  source_id TEXT,
  provider_team_id TEXT,
  observed_name TEXT,
  normalized_observed_name TEXT,
  competition TEXT,
  country TEXT,
  as_of_utc TEXT,
  resolution_status TEXT NOT NULL,
  resolved_team_id TEXT,
  resolution_method TEXT NOT NULL,
  candidate_team_ids_json TEXT,
  reason TEXT NOT NULL,
  CHECK (resolution_status IN ('RESOLVED','UNRESOLVED','AMBIGUOUS','CONFLICT')),
  CHECK ((resolution_status='RESOLVED' AND resolved_team_id IS NOT NULL) OR resolution_status<>'RESOLVED')
);

CREATE INDEX IF NOT EXISTS idx_team_alias_lookup
  ON team_aliases(team_master_version, source_id, normalized_alias, verification_status);
CREATE INDEX IF NOT EXISTS idx_team_provider_id_lookup
  ON team_source_ids(team_master_version, source_id, provider_team_id, verification_status);
CREATE INDEX IF NOT EXISTS idx_team_canonical_lookup
  ON team_master(team_master_version, canonical_name_normalized, country, competition);
CREATE TABLE IF NOT EXISTS source_registry (
  source_registry_version TEXT NOT NULL,
  field_family TEXT NOT NULL,
  canonical_source TEXT NOT NULL,
  canonical_status TEXT NOT NULL,
  preferred_candidate_source TEXT,
  fallback_source TEXT,
  fallback_status TEXT,
  definition TEXT,
  provider_definition TEXT,
  competitions TEXT,
  coverage_start TEXT,
  coverage_end TEXT,
  historical_depth TEXT,
  timestamp_available TEXT,
  timestamp_semantics TEXT,
  revision_behavior TEXT,
  row_level_audit_status TEXT,
  market_coverage_status TEXT,
  temporal_provenance_status TEXT,
  equivalence_verified INTEGER NOT NULL DEFAULT 0,
  equivalence_method TEXT,
  approved_uses TEXT,
  prohibited_uses TEXT,
  blocking_reason TEXT,
  known_issues TEXT,
  evidence_ids TEXT,
  last_audited_utc TEXT,
  dataset_admission_policy TEXT,
  PRIMARY KEY (source_registry_version, field_family)
);
CREATE TABLE IF NOT EXISTS experiment_registry (
  experiment_id TEXT PRIMARY KEY,
  hypothesis TEXT NOT NULL,
  dataset_version TEXT,
  training_period TEXT,
  target TEXT,
  feature_set TEXT,
  model TEXT,
  primary_metric TEXT,
  secondary_metrics TEXT,
  acceptance_rule TEXT,
  result TEXT,
  decision TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS ledger (
  prediction_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  operating_mode TEXT NOT NULL,
  timestamp_prediction TEXT NOT NULL,
  prediction_as_of TEXT NOT NULL,
  competition TEXT,
  season TEXT,
  kickoff TEXT,
  home_team_id TEXT,
  away_team_id TEXT,
  market TEXT,
  line REAL,
  selection TEXT,
  bookmaker TEXT,
  odds_entry REAL,
  odds_timestamp TEXT,
  price_selection_policy TEXT,
  p_model REAL,
  p_market_raw REAL,
  p_market_no_vig REAL,
  fair_odds_model REAL,
  edge_pp REAL,
  ev REAL,
  data_status TEXT,
  temporal_status TEXT,
  domain_status TEXT,
  market_status TEXT,
  uncertainty_status TEXT,
  robustness_status TEXT,
  decision TEXT NOT NULL,
  decision_reason TEXT,
  dataset_version TEXT,
  feature_set_version TEXT,
  model_version TEXT,
  calibration_version TEXT,
  config_version TEXT,
  code_version TEXT,
  random_seed INTEGER,
  training_start TEXT,
  training_end TEXT,
  calibration_start TEXT,
  calibration_end TEXT,
  home_corners INTEGER,
  away_corners INTEGER,
  total_corners INTEGER,
  result TEXT,
  profit_units REAL,
  closing_line REAL,
  closing_odds REAL,
  closing_timestamp TEXT,
  clv_status TEXT,
  clv REAL,
  created_at TEXT NOT NULL,
  CHECK (operating_mode IN ('RESEARCH','SHADOW','LIVE')),
  CHECK (decision IN ('RESEARCH_ONLY','PAPER_CANDIDATE','LIVE_CANDIDATE','WATCH','NO_BET','MARKET_UNAVAILABLE','PROBABILITY_NOT_CALCULATED','REJECTED')),
  CHECK (p_model IS NULL OR (p_model >= 0 AND p_model <= 1)),
  CHECK (p_market_raw IS NULL OR (p_market_raw >= 0 AND p_market_raw <= 1)),
  CHECK (p_market_no_vig IS NULL OR (p_market_no_vig >= 0 AND p_market_no_vig <= 1)),
  CHECK (odds_entry IS NULL OR odds_entry > 1.0),
  CHECK (home_corners IS NULL OR home_corners >= 0),
  CHECK (away_corners IS NULL OR away_corners >= 0),
  CHECK (total_corners IS NULL OR total_corners >= 0)
);
CREATE TABLE IF NOT EXISTS run_manifest (
  run_id TEXT PRIMARY KEY,
  execution_timestamp TEXT NOT NULL,
  code_version TEXT,
  dataset_version TEXT,
  feature_set_version TEXT,
  model_version TEXT,
  calibration_version TEXT,
  config_version TEXT,
  random_seed INTEGER,
  training_period TEXT,
  validation_period TEXT,
  calibration_period TEXT,
  prediction_as_of TEXT,
  package_versions_json TEXT,
  environment_information_json TEXT,
  operating_mode TEXT,
  scope TEXT,
  run_status TEXT,
  component_versions_json TEXT,
  tests_json TEXT,
  database_counts_json TEXT,
  artifact_sha256_json TEXT,
  readiness_json TEXT,
  declared_external_future_gates_json TEXT,
  limitations_json TEXT,
  manifest_path TEXT,
  manifest_sha256 TEXT,
  manifest_payload_json TEXT
);
CREATE TABLE IF NOT EXISTS data_conflicts (
  conflict_id TEXT PRIMARY KEY,
  detected_at TEXT NOT NULL,
  match_id TEXT,
  field TEXT,
  source_a TEXT,
  value_a TEXT,
  source_b TEXT,
  value_b TEXT,
  resolution TEXT,
  material INTEGER NOT NULL,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS change_registry (
  change_id TEXT PRIMARY KEY,
  date TEXT NOT NULL,
  hypothesis TEXT,
  reason TEXT,
  old_version TEXT,
  candidate_version TEXT,
  data_used TEXT,
  features_changed TEXT,
  parameters_changed TEXT,
  oos_results TEXT,
  calibration_effect TEXT,
  robustness_effect TEXT,
  decision TEXT
);

-- Step D: canonical dataset schema v1.0
CREATE TABLE IF NOT EXISTS dataset_versions (
  dataset_version TEXT PRIMARY KEY,
  dataset_schema_version TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  parent_dataset_version TEXT,
  source_registry_version TEXT,
  team_master_version TEXT,
  code_version TEXT,
  config_version TEXT,
  raw_manifest_sha256 TEXT,
  row_count INTEGER CHECK (row_count IS NULL OR row_count >= 0),
  immutable INTEGER NOT NULL DEFAULT 1 CHECK (immutable IN (0,1)),
  notes TEXT
);

CREATE TABLE IF NOT EXISTS canonical_matches (
  dataset_version TEXT NOT NULL,
  match_id TEXT NOT NULL,
  competition TEXT NOT NULL,
  season TEXT NOT NULL,
  kickoff_utc TEXT NOT NULL,
  kickoff_local TEXT,
  kickoff_timezone_original TEXT,
  home_team_id TEXT NOT NULL,
  away_team_id TEXT NOT NULL,
  home_corners INTEGER,
  away_corners INTEGER,
  total_corners INTEGER,
  match_status TEXT NOT NULL,
  source_id TEXT NOT NULL,
  provider_match_id TEXT,
  source_version TEXT,
  collection_timestamp TEXT NOT NULL,
  record_publication_time TEXT,
  record_revision_time TEXT,
  row_quality_status TEXT NOT NULL DEFAULT 'NOT_AUDITED',
  PRIMARY KEY (dataset_version, match_id),
  FOREIGN KEY (dataset_version) REFERENCES dataset_versions(dataset_version),
  CHECK (home_team_id <> away_team_id),
  CHECK (home_corners IS NULL OR home_corners >= 0),
  CHECK (away_corners IS NULL OR away_corners >= 0),
  CHECK (total_corners IS NULL OR total_corners >= 0),
  CHECK (
    home_corners IS NULL OR away_corners IS NULL OR total_corners IS NULL
    OR total_corners = home_corners + away_corners
  ),
  CHECK (match_status IN ('SCHEDULED','COMPLETED','POSTPONED','CANCELLED','ABANDONED','SUSPENDED','UNKNOWN')),
  CHECK (row_quality_status IN ('DATA_PASS','DATA_PARTIAL','DATA_FAIL','NOT_AUDITED'))
);

CREATE TABLE IF NOT EXISTS match_source_map (
  source_id TEXT NOT NULL,
  provider_match_id TEXT NOT NULL,
  match_id TEXT NOT NULL,
  valid_from_utc TEXT,
  valid_to_utc TEXT,
  first_collection_time TEXT,
  last_collection_time TEXT,
  PRIMARY KEY (source_id, provider_match_id)
);

CREATE TABLE IF NOT EXISTS source_observations (
  observation_id TEXT PRIMARY KEY,
  match_id TEXT,
  source_id TEXT NOT NULL,
  source_version TEXT,
  provider_match_id TEXT,
  field TEXT NOT NULL,
  value_json TEXT NOT NULL,
  value_type TEXT NOT NULL,
  event_time TEXT,
  publication_time TEXT,
  collection_time TEXT NOT NULL,
  revision_time TEXT,
  information_time TEXT,
  information_time_basis TEXT NOT NULL,
  temporal_usable INTEGER NOT NULL DEFAULT 0,
  raw_artifact_sha256 TEXT,
  raw_artifact_path TEXT,
  dataset_version TEXT,
  FOREIGN KEY (dataset_version) REFERENCES dataset_versions(dataset_version),
  CHECK (value_type IN ('INTEGER','REAL','TEXT','BOOLEAN','JSON','NULL')),
  CHECK (information_time_basis IN ('PROVIDER_PUBLICATION','PROSPECTIVE_COLLECTION','UNKNOWN')),
  CHECK (temporal_usable IN (0,1)),
  CHECK (temporal_usable = 0 OR information_time IS NOT NULL),
  CHECK (information_time_basis <> 'UNKNOWN' OR temporal_usable = 0),
  CHECK (information_time_basis <> 'PROVIDER_PUBLICATION' OR (publication_time IS NOT NULL AND information_time = publication_time)),
  CHECK (information_time_basis <> 'PROSPECTIVE_COLLECTION' OR information_time = collection_time),
  CHECK (raw_artifact_sha256 IS NULL OR length(raw_artifact_sha256) = 64)
);

CREATE INDEX IF NOT EXISTS idx_canonical_matches_kickoff
  ON canonical_matches(dataset_version, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_canonical_matches_teams
  ON canonical_matches(dataset_version, home_team_id, away_team_id);
CREATE INDEX IF NOT EXISTS idx_source_observations_match_field
  ON source_observations(match_id, field, source_id);
CREATE INDEX IF NOT EXISTS idx_source_observations_information_time
  ON source_observations(information_time);

-- Step F: historical availability registry v1.0
CREATE TABLE IF NOT EXISTS historical_availability (
  availability_version TEXT NOT NULL,
  competition TEXT NOT NULL,
  season TEXT NOT NULL,
  season_state TEXT NOT NULL,
  variable TEXT NOT NULL,
  source_candidate TEXT NOT NULL,
  availability_status TEXT NOT NULL,
  evidence_level TEXT NOT NULL,
  temporal_scope TEXT NOT NULL,
  earliest_possible_date TEXT,
  latest_possible_date TEXT,
  exact_row_coverage_known INTEGER NOT NULL CHECK (exact_row_coverage_known IN (0,1)),
  asof_reconstructible INTEGER NOT NULL CHECK (asof_reconstructible IN (0,1)),
  potential_asof_reconstructible INTEGER NOT NULL CHECK (potential_asof_reconstructible IN (0,1)),
  evidence_url TEXT,
  secondary_verification TEXT NOT NULL,
  secondary_evidence_url TEXT,
  evidence_note TEXT NOT NULL,
  blocking_reason TEXT NOT NULL,
  PRIMARY KEY (availability_version, competition, season, variable),
  CHECK (season_state IN ('COMPLETED','IN_PROGRESS')),
  CHECK (asof_reconstructible = 0 OR potential_asof_reconstructible = 1)
);
CREATE INDEX IF NOT EXISTS idx_historical_availability_lookup
  ON historical_availability(availability_version, competition, variable, season);

-- Step G: missingness audit registry v1.0
CREATE TABLE IF NOT EXISTS missingness_audit (
  missingness_version TEXT NOT NULL,
  competition TEXT NOT NULL,
  season TEXT NOT NULL,
  season_state TEXT NOT NULL,
  variable TEXT NOT NULL,
  fields TEXT NOT NULL,
  source_candidate TEXT NOT NULL,
  measurement_status TEXT NOT NULL,
  row_level_measured INTEGER NOT NULL DEFAULT 0 CHECK (row_level_measured IN (0,1)),
  rows_total INTEGER CHECK (rows_total IS NULL OR rows_total >= 0),
  rows_missing INTEGER CHECK (rows_missing IS NULL OR rows_missing >= 0),
  missing_rate REAL CHECK (missing_rate IS NULL OR (missing_rate >= 0 AND missing_rate <= 1)),
  rows_invalid INTEGER CHECK (rows_invalid IS NULL OR rows_invalid >= 0),
  rows_usable INTEGER CHECK (rows_usable IS NULL OR rows_usable >= 0),
  coverage_rate REAL CHECK (coverage_rate IS NULL OR (coverage_rate >= 0 AND coverage_rate <= 1)),
  raw_sha256 TEXT,
  evidence_basis TEXT NOT NULL,
  evidence_ref TEXT,
  threshold_evaluation TEXT NOT NULL,
  data_quality_state TEXT NOT NULL,
  source_conflict INTEGER NOT NULL DEFAULT 0 CHECK (source_conflict IN (0,1)),
  blocking_reason TEXT NOT NULL,
  notes TEXT,
  PRIMARY KEY (missingness_version, competition, season, variable),
  CHECK (raw_sha256 IS NULL OR length(raw_sha256) = 64),
  CHECK (measurement_status <> 'ROW_MEASURED' OR row_level_measured = 1),
  CHECK (measurement_status <> 'STRUCTURAL_100_PERCENT' OR (row_level_measured = 0 AND missing_rate = 1.0 AND coverage_rate = 0.0)),
  CHECK (source_conflict = 0 OR missing_rate IS NULL),
  CHECK (data_quality_state IN ('DATA_PASS','DATA_PARTIAL','DATA_FAIL','NOT_EVALUATED'))
);
CREATE INDEX IF NOT EXISTS idx_missingness_lookup
  ON missingness_audit(missingness_version, competition, variable, season);

-- Step H: odds audit and immutable market-observation infrastructure v1.0
CREATE TABLE IF NOT EXISTS odds_audit_runs (
  odds_audit_version TEXT PRIMARY KEY,
  created_at_utc TEXT NOT NULL,
  status TEXT NOT NULL,
  source_registry_version TEXT NOT NULL,
  config_version TEXT NOT NULL,
  authenticated_coverage_executed INTEGER NOT NULL DEFAULT 0 CHECK (authenticated_coverage_executed IN (0,1)),
  exact_coverage_known INTEGER NOT NULL DEFAULT 0 CHECK (exact_coverage_known IN (0,1)),
  notes TEXT
);

CREATE TABLE IF NOT EXISTS odds_provider_capabilities (
  odds_audit_version TEXT NOT NULL,
  provider TEXT NOT NULL,
  capability TEXT NOT NULL,
  status TEXT NOT NULL,
  evidence_ref TEXT,
  earliest_history_utc TEXT,
  retention_policy TEXT,
  timestamp_semantics TEXT,
  blocking_reason TEXT,
  PRIMARY KEY (odds_audit_version, provider, capability),
  FOREIGN KEY (odds_audit_version) REFERENCES odds_audit_runs(odds_audit_version)
);

CREATE TABLE IF NOT EXISTS odds_market_mappings (
  odds_audit_version TEXT NOT NULL,
  provider TEXT NOT NULL,
  laboratory_market TEXT NOT NULL,
  provider_market_key TEXT NOT NULL,
  mapping_status TEXT NOT NULL,
  team_side_semantics TEXT,
  evidence_ref TEXT,
  PRIMARY KEY (odds_audit_version, provider, laboratory_market, provider_market_key),
  FOREIGN KEY (odds_audit_version) REFERENCES odds_audit_runs(odds_audit_version),
  CHECK (laboratory_market IN ('TOTAL_CORNERS','HOME_TEAM_TOTAL_CORNERS','AWAY_TEAM_TOTAL_CORNERS'))
);

CREATE TABLE IF NOT EXISTS odds_bookmaker_catalogue (
  odds_audit_version TEXT NOT NULL,
  provider TEXT NOT NULL,
  region TEXT NOT NULL,
  bookmaker_key TEXT NOT NULL,
  bookmaker_name TEXT NOT NULL,
  catalogue_status TEXT NOT NULL,
  target_corner_coverage_status TEXT NOT NULL,
  allowed_for_lab INTEGER NOT NULL DEFAULT 0 CHECK (allowed_for_lab IN (0,1)),
  note TEXT,
  PRIMARY KEY (odds_audit_version, provider, region, bookmaker_key),
  FOREIGN KEY (odds_audit_version) REFERENCES odds_audit_runs(odds_audit_version),
  CHECK (allowed_for_lab = 0 OR target_corner_coverage_status = 'PASS')
);

CREATE TABLE IF NOT EXISTS odds_coverage_measurements (
  odds_audit_version TEXT NOT NULL,
  provider TEXT NOT NULL,
  competition TEXT NOT NULL,
  sport_key TEXT,
  laboratory_market TEXT NOT NULL,
  events_eligible INTEGER,
  events_with_market INTEGER,
  coverage_rate REAL CHECK (coverage_rate IS NULL OR (coverage_rate >= 0 AND coverage_rate <= 1)),
  bookmakers_with_market INTEGER,
  valid_pairs INTEGER,
  invalid_pairs INTEGER,
  timestamp_complete_rate REAL CHECK (timestamp_complete_rate IS NULL OR (timestamp_complete_rate >= 0 AND timestamp_complete_rate <= 1)),
  line_min REAL,
  line_max REAL,
  audit_status TEXT NOT NULL,
  evidence_basis TEXT NOT NULL,
  blocking_reason TEXT,
  PRIMARY KEY (odds_audit_version, provider, competition, laboratory_market),
  FOREIGN KEY (odds_audit_version) REFERENCES odds_audit_runs(odds_audit_version),
  CHECK (laboratory_market IN ('TOTAL_CORNERS','HOME_TEAM_TOTAL_CORNERS','AWAY_TEAM_TOTAL_CORNERS'))
);

CREATE TABLE IF NOT EXISTS odds_raw_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  requested_as_of_utc TEXT,
  provider_snapshot_utc TEXT,
  collection_time_utc TEXT NOT NULL,
  raw_path TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL CHECK (length(raw_sha256)=64),
  immutable INTEGER NOT NULL DEFAULT 1 CHECK (immutable IN (0,1)),
  CHECK (requested_as_of_utc IS NULL OR provider_snapshot_utc IS NULL OR provider_snapshot_utc <= requested_as_of_utc)
);

CREATE TABLE IF NOT EXISTS odds_observations (
  observation_id TEXT PRIMARY KEY,
  snapshot_id TEXT,
  provider TEXT NOT NULL,
  event_id TEXT NOT NULL,
  competition TEXT,
  kickoff_utc TEXT NOT NULL,
  bookmaker TEXT NOT NULL,
  market TEXT NOT NULL,
  subject TEXT,
  laboratory_market TEXT,
  line REAL NOT NULL CHECK (line >= 0),
  selection TEXT NOT NULL,
  decimal_odds REAL NOT NULL CHECK (decimal_odds > 1.0),
  odds_timestamp TEXT NOT NULL,
  collection_timestamp TEXT NOT NULL,
  source TEXT NOT NULL,
  verification_status TEXT NOT NULL,
  FOREIGN KEY (snapshot_id) REFERENCES odds_raw_snapshots(snapshot_id),
  CHECK (selection IN ('OVER','UNDER')),
  CHECK (abs(line*2 - CAST(line*2 AS INTEGER)) < 0.0000001),
  CHECK ((CAST(round(line*2) AS INTEGER) % 2) = 1),
  CHECK (odds_timestamp < kickoff_utc),
  CHECK (laboratory_market IS NULL OR laboratory_market IN ('TOTAL_CORNERS','HOME_TEAM_TOTAL_CORNERS','AWAY_TEAM_TOTAL_CORNERS')),
  CHECK (market <> 'alternate_team_totals_corners' OR subject IS NOT NULL),
  CHECK (verification_status IN ('VERIFIED_PROVIDER','USER_ASSERTED','UNVERIFIED'))
);

CREATE INDEX IF NOT EXISTS idx_odds_observations_contract
  ON odds_observations(provider,event_id,bookmaker,market,line,odds_timestamp);
CREATE INDEX IF NOT EXISTS idx_odds_observations_kickoff
  ON odds_observations(kickoff_utc,competition);

-- v3.1: additive evidence inventory and TOTAL_CORNERS audit result.
-- These tables do not overwrite or reinterpret any H.1.3/H.1.4 artifact.
CREATE TABLE IF NOT EXISTS odds_evidence_packages_v31 (
  evidence_id TEXT PRIMARY KEY,
  manifest_version TEXT NOT NULL,
  path TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL CHECK (length(sha256)=64),
  bytes INTEGER NOT NULL CHECK (bytes >= 0),
  evidence_role TEXT NOT NULL,
  original_result TEXT NOT NULL,
  immutable INTEGER NOT NULL DEFAULT 1 CHECK (immutable IN (0,1))
);

CREATE TABLE IF NOT EXISTS odds_audit_v31_runs (
  run_id TEXT PRIMARY KEY,
  execution_timestamp_utc TEXT NOT NULL,
  protocol_version TEXT NOT NULL,
  config_version TEXT NOT NULL,
  operating_mode TEXT NOT NULL CHECK (operating_mode='SHADOW'),
  operational_market TEXT NOT NULL CHECK (operational_market='TOTAL_CORNERS'),
  engineering_validation_status TEXT NOT NULL,
  historical_coverage_proof_status TEXT NOT NULL,
  step_h_status TEXT NOT NULL,
  step_i_allowed INTEGER NOT NULL CHECK (step_i_allowed IN (0,1)),
  paid_access_used INTEGER NOT NULL CHECK (paid_access_used IN (0,1)),
  result_json_sha256 TEXT NOT NULL CHECK (length(result_json_sha256)=64)
);

CREATE TABLE IF NOT EXISTS odds_audit_v31_legacy_cells (
  run_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  competition TEXT NOT NULL,
  market TEXT NOT NULL CHECK (market='TOTAL_CORNERS'),
  horizon_minutes INTEGER NOT NULL CHECK (horizon_minutes IN (60,30,15)),
  eligible_fixtures INTEGER NOT NULL CHECK (eligible_fixtures >= 0),
  covered_fixtures INTEGER NOT NULL CHECK (covered_fixtures >= 0),
  coverage_rate REAL NOT NULL CHECK (coverage_rate >= 0 AND coverage_rate <= 1),
  original_status TEXT NOT NULL,
  evidence_role TEXT NOT NULL,
  promotion_eligible INTEGER NOT NULL CHECK (promotion_eligible IN (0,1)),
  PRIMARY KEY (run_id, evidence_id, competition, market, horizon_minutes),
  FOREIGN KEY (run_id) REFERENCES odds_audit_v31_runs(run_id),
  FOREIGN KEY (evidence_id) REFERENCES odds_evidence_packages_v31(evidence_id)
);

-- v3.1.2: prospective TOTAL_CORNERS denominator and collection ledger.
CREATE TABLE IF NOT EXISTS odds_prospective_v31_cohorts (
  cohort_id TEXT PRIMARY KEY,
  protocol_version TEXT NOT NULL,
  enrolled_at_utc TEXT NOT NULL,
  source_url TEXT NOT NULL,
  source_sha256 TEXT NOT NULL CHECK (length(source_sha256)=64),
  eligible_fixtures INTEGER NOT NULL CHECK (eligible_fixtures >= 0),
  target_odds_observed_during_enrollment INTEGER NOT NULL CHECK (target_odds_observed_during_enrollment=0),
  immutable INTEGER NOT NULL DEFAULT 1 CHECK (immutable=1)
);

CREATE TABLE IF NOT EXISTS odds_prospective_v31_fixtures (
  cohort_id TEXT NOT NULL,
  fixture_id TEXT NOT NULL,
  competition TEXT NOT NULL,
  sport_key TEXT NOT NULL,
  kickoff_utc TEXT NOT NULL,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  PRIMARY KEY (cohort_id, fixture_id),
  FOREIGN KEY (cohort_id) REFERENCES odds_prospective_v31_cohorts(cohort_id)
);

CREATE TABLE IF NOT EXISTS odds_prospective_v31_collection_runs (
  run_id TEXT PRIMARY KEY,
  cohort_id TEXT NOT NULL,
  execution_timestamp_utc TEXT NOT NULL,
  due_tasks INTEGER NOT NULL CHECK (due_tasks >= 0),
  odds_requests INTEGER NOT NULL CHECK (odds_requests >= 0),
  status TEXT NOT NULL,
  paid_access_used INTEGER NOT NULL CHECK (paid_access_used=0),
  manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256)=64),
  FOREIGN KEY (cohort_id) REFERENCES odds_prospective_v31_cohorts(cohort_id)
);
