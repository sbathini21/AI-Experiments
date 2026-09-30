-- Generated from app/models.py. PostgreSQL 15+.

CREATE TABLE countries (
	code VARCHAR(2) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	currency VARCHAR(3), 
	timezone VARCHAR(64), 
	regulator VARCHAR(100), 
	PRIMARY KEY (code)
);


CREATE TABLE jobs (
	id VARCHAR(36) NOT NULL, 
	kind VARCHAR(40) NOT NULL, 
	dedupe_key VARCHAR(200), 
	payload JSONB NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	progress JSONB, 
	result JSONB, 
	error TEXT, 
	attempts INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (dedupe_key)
);

CREATE INDEX ix_jobs_status ON jobs (status);

CREATE TABLE sectors (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);


CREATE TABLE sources (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	domain VARCHAR(200), 
	source_type VARCHAR(40) NOT NULL, 
	credibility_tier INTEGER NOT NULL, 
	country_code VARCHAR(2), 
	license_notes TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (domain)
);


CREATE TABLE users (
	id VARCHAR(36) NOT NULL, 
	email VARCHAR(320) NOT NULL, 
	password_hash VARCHAR(200), 
	display_name VARCHAR(100), 
	preferred_mode VARCHAR(20) NOT NULL, 
	is_admin BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_login_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE exchanges (
	mic VARCHAR(10) NOT NULL, 
	code VARCHAR(20) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	country_code VARCHAR(2) NOT NULL, 
	figi_exch_code VARCHAR(10), 
	PRIMARY KEY (mic), 
	FOREIGN KEY(country_code) REFERENCES countries (code)
);


CREATE TABLE industries (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	sector_id VARCHAR(36), 
	keywords JSONB, 
	PRIMARY KEY (id), 
	UNIQUE (name), 
	FOREIGN KEY(sector_id) REFERENCES sectors (id) ON DELETE SET NULL
);

CREATE INDEX ix_industries_sector_id ON industries (sector_id);

CREATE TABLE watchlists (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_watchlists_user_id ON watchlists (user_id);

CREATE TABLE companies (
	id VARCHAR(36) NOT NULL, 
	legal_name VARCHAR(300) NOT NULL, 
	short_name VARCHAR(200), 
	aliases JSONB, 
	country_code VARCHAR(2), 
	industry_id VARCHAR(36), 
	industry_text VARCHAR(200), 
	website VARCHAR(300), 
	ir_url VARCHAR(300), 
	linkedin_url VARCHAR(300), 
	lei VARCHAR(20), 
	identifiers JSONB, 
	profile JSONB, 
	profile_refreshed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(country_code) REFERENCES countries (code), 
	FOREIGN KEY(industry_id) REFERENCES industries (id) ON DELETE SET NULL
);

CREATE INDEX ix_companies_legal_name ON companies (legal_name);
CREATE INDEX ix_companies_industry_id ON companies (industry_id);

CREATE TABLE alerts (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36), 
	event_types JSONB, 
	min_materiality VARCHAR(10) NOT NULL, 
	channel VARCHAR(20) NOT NULL, 
	active BOOLEAN NOT NULL, 
	last_triggered_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
);

CREATE INDEX ix_alerts_user_id ON alerts (user_id);
CREATE INDEX ix_alerts_company_id ON alerts (company_id);

CREATE TABLE company_daily_reports (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	mode VARCHAR(20) NOT NULL, 
	window_days INTEGER NOT NULL, 
	generated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	data_freshness TIMESTAMP WITH TIME ZONE, 
	report JSONB NOT NULL, 
	voice_script TEXT, 
	synthesizer VARCHAR(60) NOT NULL, 
	event_ids JSONB, 
	stats JSONB, 
	PRIMARY KEY (id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
);

CREATE INDEX ix_company_daily_reports_company_id ON company_daily_reports (company_id);
CREATE INDEX ix_reports_company_mode_time ON company_daily_reports (company_id, mode, generated_at);

CREATE TABLE competitors (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	competitor_company_id VARCHAR(36), 
	competitor_name VARCHAR(300) NOT NULL, 
	basis TEXT, 
	source_id VARCHAR(36), 
	confidence FLOAT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (company_id, competitor_company_id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(competitor_company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(source_id) REFERENCES sources (id) ON DELETE SET NULL
);

CREATE INDEX ix_competitors_competitor_company_id ON competitors (competitor_company_id);
CREATE INDEX ix_competitors_company_id ON competitors (company_id);
CREATE INDEX ix_competitors_source_id ON competitors (source_id);

CREATE TABLE documents (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	source_id VARCHAR(36), 
	doc_type VARCHAR(30) NOT NULL, 
	connector VARCHAR(40) NOT NULL, 
	url TEXT NOT NULL, 
	title TEXT NOT NULL, 
	snippet TEXT, 
	body_text TEXT, 
	author VARCHAR(200), 
	language VARCHAR(10), 
	published_at TIMESTAMP WITH TIME ZONE, 
	retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	credibility_tier INTEGER NOT NULL, 
	match_confidence FLOAT NOT NULL, 
	processed BOOLEAN NOT NULL, 
	raw JSONB, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_document_hash UNIQUE (company_id, content_hash), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(source_id) REFERENCES sources (id) ON DELETE SET NULL
);

CREATE INDEX ix_documents_company_id ON documents (company_id);
CREATE INDEX ix_documents_source_id ON documents (source_id);
CREATE INDEX ix_documents_published_at ON documents (published_at);

CREATE TABLE events (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	event_type VARCHAR(50) NOT NULL, 
	category VARCHAR(30) NOT NULL, 
	title TEXT NOT NULL, 
	description TEXT, 
	event_date DATE, 
	announcement_date DATE, 
	detected_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	materiality VARCHAR(10) NOT NULL, 
	materiality_score FLOAT NOT NULL, 
	confidence FLOAT NOT NULL, 
	verification VARCHAR(20) NOT NULL, 
	claim_type VARCHAR(20) NOT NULL, 
	sentiment VARCHAR(10), 
	impact_areas JSONB, 
	related_company_ids JSONB, 
	financial_impact TEXT, 
	fingerprint VARCHAR(64) NOT NULL, 
	extracted_by VARCHAR(40) NOT NULL, 
	evidence_quote TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
);

CREATE INDEX ix_events_event_type ON events (event_type);
CREATE INDEX ix_events_company_date ON events (company_id, event_date);
CREATE INDEX ix_events_company_id ON events (company_id);
CREATE INDEX ix_events_fingerprint ON events (fingerprint);

CREATE TABLE securities (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	exchange_mic VARCHAR(10) NOT NULL, 
	ticker VARCHAR(40) NOT NULL, 
	isin VARCHAR(12), 
	figi VARCHAR(12), 
	instrument_type VARCHAR(30) NOT NULL, 
	currency VARCHAR(3), 
	is_primary BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_security_exchange_ticker UNIQUE (exchange_mic, ticker), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(exchange_mic) REFERENCES exchanges (mic)
);

CREATE INDEX ix_securities_isin ON securities (isin);
CREATE INDEX ix_securities_ticker ON securities (ticker);
CREATE INDEX ix_securities_company_id ON securities (company_id);

CREATE TABLE watchlist_items (
	id VARCHAR(36) NOT NULL, 
	watchlist_id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	added_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (watchlist_id, company_id), 
	FOREIGN KEY(watchlist_id) REFERENCES watchlists (id) ON DELETE CASCADE, 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
);

CREATE INDEX ix_watchlist_items_watchlist_id ON watchlist_items (watchlist_id);
CREATE INDEX ix_watchlist_items_company_id ON watchlist_items (company_id);

CREATE TABLE articles (
	document_id VARCHAR(36) NOT NULL, 
	publisher VARCHAR(200), 
	section VARCHAR(100), 
	tone FLOAT, 
	PRIMARY KEY (document_id), 
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
);


CREATE TABLE audio_reports (
	id VARCHAR(36) NOT NULL, 
	report_id VARCHAR(36) NOT NULL, 
	provider VARCHAR(40) NOT NULL, 
	voice VARCHAR(40), 
	duration_seconds FLOAT, 
	storage_path TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(report_id) REFERENCES company_daily_reports (id) ON DELETE CASCADE
);

CREATE INDEX ix_audio_reports_report_id ON audio_reports (report_id);

CREATE TABLE citations (
	id VARCHAR(36) NOT NULL, 
	report_id VARCHAR(36) NOT NULL, 
	section VARCHAR(40) NOT NULL, 
	item_index INTEGER NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	event_id VARCHAR(36), 
	PRIMARY KEY (id), 
	FOREIGN KEY(report_id) REFERENCES company_daily_reports (id) ON DELETE CASCADE, 
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE, 
	FOREIGN KEY(event_id) REFERENCES events (id) ON DELETE SET NULL
);

CREATE INDEX ix_citations_document_id ON citations (document_id);
CREATE INDEX ix_citations_event_id ON citations (event_id);
CREATE INDEX ix_citations_report_id ON citations (report_id);

CREATE TABLE event_sources (
	id VARCHAR(36) NOT NULL, 
	event_id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (event_id, document_id), 
	FOREIGN KEY(event_id) REFERENCES events (id) ON DELETE CASCADE, 
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
);

CREATE INDEX ix_event_sources_document_id ON event_sources (document_id);
CREATE INDEX ix_event_sources_event_id ON event_sources (event_id);

CREATE TABLE filings (
	document_id VARCHAR(36) NOT NULL, 
	regulator VARCHAR(40) NOT NULL, 
	form_type VARCHAR(40), 
	filing_id VARCHAR(100), 
	items JSONB, 
	period_of_report DATE, 
	PRIMARY KEY (document_id), 
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
);


CREATE TABLE financial_metrics (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	metric VARCHAR(60) NOT NULL, 
	period_type VARCHAR(10) NOT NULL, 
	period_end DATE NOT NULL, 
	value NUMERIC(24, 4) NOT NULL, 
	currency VARCHAR(3), 
	unit VARCHAR(20), 
	source_document_id VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (company_id, metric, period_end, period_type, source_document_id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(source_document_id) REFERENCES documents (id) ON DELETE SET NULL
);

CREATE INDEX ix_financial_metrics_company_id ON financial_metrics (company_id);
CREATE INDEX ix_financial_metrics_source_document_id ON financial_metrics (source_document_id);

CREATE TABLE industry_events (
	id VARCHAR(36) NOT NULL, 
	industry_id VARCHAR(36), 
	country_code VARCHAR(2), 
	title TEXT NOT NULL, 
	description TEXT, 
	event_date DATE, 
	document_id VARCHAR(36), 
	affected_company_ids JSONB, 
	materiality VARCHAR(10) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(industry_id) REFERENCES industries (id) ON DELETE CASCADE, 
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE SET NULL
);

CREATE INDEX ix_industry_events_industry_id ON industry_events (industry_id);
CREATE INDEX ix_industry_events_document_id ON industry_events (document_id);

CREATE TABLE management_statements (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36), 
	speaker VARCHAR(200), 
	speaker_role VARCHAR(100), 
	statement TEXT NOT NULL, 
	statement_date DATE, 
	is_forward_looking BOOLEAN NOT NULL, 
	topic VARCHAR(60), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE SET NULL
);

CREATE INDEX ix_management_statements_company_id ON management_statements (company_id);
CREATE INDEX ix_management_statements_document_id ON management_statements (document_id);

CREATE TABLE market_data (
	id VARCHAR(36) NOT NULL, 
	security_id VARCHAR(36) NOT NULL, 
	trade_date DATE NOT NULL, 
	open NUMERIC(18, 4), 
	high NUMERIC(18, 4), 
	low NUMERIC(18, 4), 
	close NUMERIC(18, 4), 
	volume NUMERIC(24, 2), 
	provider VARCHAR(40), 
	PRIMARY KEY (id), 
	UNIQUE (security_id, trade_date), 
	FOREIGN KEY(security_id) REFERENCES securities (id) ON DELETE CASCADE
);

CREATE INDEX ix_market_data_security_id ON market_data (security_id);

CREATE TABLE research_queries (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36), 
	client_key VARCHAR(64), 
	raw_query TEXT NOT NULL, 
	input_mode VARCHAR(10) NOT NULL, 
	parsed JSONB, 
	company_id VARCHAR(36), 
	report_id VARCHAR(36), 
	job_id VARCHAR(36), 
	triggered_research BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE SET NULL, 
	FOREIGN KEY(report_id) REFERENCES company_daily_reports (id) ON DELETE SET NULL
);

CREATE INDEX ix_research_queries_user_id ON research_queries (user_id);
CREATE INDEX ix_research_queries_company_id ON research_queries (company_id);
CREATE INDEX ix_research_queries_report_id ON research_queries (report_id);
CREATE INDEX ix_research_queries_client_key ON research_queries (client_key);

CREATE TABLE management_guidance (
	id VARCHAR(36) NOT NULL, 
	company_id VARCHAR(36) NOT NULL, 
	statement_id VARCHAR(36), 
	metric VARCHAR(100) NOT NULL, 
	target_value VARCHAR(100), 
	target_unit VARCHAR(40), 
	deadline_text VARCHAR(60), 
	deadline_date DATE, 
	given_on DATE, 
	status VARCHAR(30) NOT NULL, 
	superseded_by_id VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(statement_id) REFERENCES management_statements (id) ON DELETE SET NULL, 
	FOREIGN KEY(superseded_by_id) REFERENCES management_guidance (id) ON DELETE SET NULL
);

CREATE INDEX ix_management_guidance_company_id ON management_guidance (company_id);
CREATE INDEX ix_management_guidance_statement_id ON management_guidance (statement_id);
CREATE INDEX ix_management_guidance_superseded_by_id ON management_guidance (superseded_by_id);

CREATE TABLE guidance_tracking (
	id VARCHAR(36) NOT NULL, 
	guidance_id VARCHAR(36) NOT NULL, 
	event_id VARCHAR(36), 
	document_id VARCHAR(36), 
	previous_status VARCHAR(30), 
	new_status VARCHAR(30) NOT NULL, 
	rationale TEXT NOT NULL, 
	assessed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(guidance_id) REFERENCES management_guidance (id) ON DELETE CASCADE, 
	FOREIGN KEY(event_id) REFERENCES events (id) ON DELETE SET NULL, 
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE SET NULL
);

CREATE INDEX ix_guidance_tracking_event_id ON guidance_tracking (event_id);
CREATE INDEX ix_guidance_tracking_document_id ON guidance_tracking (document_id);
CREATE INDEX ix_guidance_tracking_guidance_id ON guidance_tracking (guidance_id);

