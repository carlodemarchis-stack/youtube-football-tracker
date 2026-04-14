-- Migration v6: AI chat token tracking
CREATE TABLE IF NOT EXISTS ai_usage (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_email TEXT NOT NULL,
    input_tokens INT DEFAULT 0,
    output_tokens INT DEFAULT 0,
    model TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Add token quota to user profiles (0 = unlimited for admins)
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS ai_token_budget INT DEFAULT 50000;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS ai_tokens_used INT DEFAULT 0;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS ai_budget_reset DATE DEFAULT CURRENT_DATE;
