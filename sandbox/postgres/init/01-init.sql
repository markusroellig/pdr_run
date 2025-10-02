
-- PostgreSQL initialization script for PDR sandbox
CREATE TABLE IF NOT EXISTS connection_test (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message VARCHAR(255)
);

INSERT INTO connection_test (message) VALUES ('PostgreSQL sandbox initialized successfully');
