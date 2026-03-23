-- nrev-lite Database Schema — Add user prompt to learning logs
-- Run: psql -U nrev_lite -d nrev_lite -f migrations/011_learning_prompt.sql

-- Add user_prompt column to capture the original user intent for admin context
ALTER TABLE learning_logs ADD COLUMN IF NOT EXISTS user_prompt TEXT;

-- Add index for text search on prompts (optional, helps admin review)
CREATE INDEX IF NOT EXISTS idx_learning_logs_user_prompt ON learning_logs USING gin(to_tsvector('english', coalesce(user_prompt, '')));
