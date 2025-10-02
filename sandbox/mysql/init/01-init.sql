
-- MySQL initialization script for PDR sandbox
-- Note: Database 'pdr_test' and user 'pdr_user' are auto-created by Docker

-- Create a test table to verify connection
CREATE TABLE IF NOT EXISTS connection_test (
    id INT AUTO_INCREMENT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message VARCHAR(255)
);

INSERT INTO connection_test (message) VALUES ('MySQL sandbox initialized successfully');
