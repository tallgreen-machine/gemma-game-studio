#!/bin/bash
echo "Installing PostgreSQL and creating Gemma 4 Memory schemas..."

# Install PostgreSQL
sudo apt-get update
sudo apt-get install -y postgresql postgresql-contrib

# Start Service
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Create Database and User
sudo -u postgres psql -c "CREATE USER gemma_user WITH PASSWORD 'epiphany_db_pass';"
sudo -u postgres psql -c "CREATE DATABASE gemma_db OWNER gemma_user;"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE gemma_db TO gemma_user;"

# Create Tables
sudo -u postgres psql -d gemma_db -c "
CREATE TABLE IF NOT EXISTS agent_state (
    key VARCHAR(255) PRIMARY KEY,
    value TEXT
);
"

sudo -u postgres psql -d gemma_db -c "
CREATE TABLE IF NOT EXISTS reminders (
    id SERIAL PRIMARY KEY,
    note TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"

echo "PostgreSQL installed and configured!"
echo "Please restart the python web server to connect to the DB."
