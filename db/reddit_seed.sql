-- Seed the reddit_subreddits table with the 17 mapped clubs.
-- channel_id values are live from your current DB — paste as-is.
-- Run AFTER reddit_schema.sql.

insert into reddit_subreddits (subreddit, channel_id, is_official) values
  ('Juve',             '9b27ff36-d4b7-4b73-8b2c-ae4807fb7d97', false),  -- Juventus
  ('FCInterMilan',     '0107f05c-025a-4032-bd10-92101f942e2d', false),  -- Inter
  ('ACMilan',          'd3eabe38-92d0-4206-a384-744552a7054f', false),  -- AC Milan
  ('sscnapoli',        'f6627ad8-0c8c-497d-b10e-dce915fc425c', false),  -- SSC Napoli
  ('ASRoma',           '0536224f-5476-42ff-b61c-29d0aebc07c4', false),  -- AS Roma
  ('reddevils',        '8fe93bfd-4648-4795-a036-fa4099af59d9', false),  -- Man United
  ('LiverpoolFC',      '078def72-ec2b-4bb2-bca7-92a764d44476', false),  -- Liverpool
  ('Gunners',          '800772b0-da41-488b-8450-e3d34af9dccd', false),  -- Arsenal
  ('MCFC',             '097fa134-6153-44e7-bc22-2007e11edb0d', false),  -- Man City
  ('chelseafc',        '61d74401-5958-4329-b29f-a7ab46708a8e', false),  -- Chelsea
  ('coys',             'faea9aeb-07e7-4dea-92b7-f308d342d559', false),  -- Tottenham
  ('realmadrid',       'dd5a6cfa-10c0-4f3a-8756-457471a29416', false),  -- Real Madrid
  ('Barca',            'dee1474d-e340-40d1-87fd-ae48bec9ec3e', false),  -- Barcelona
  ('atletico',         'a572531a-c330-4ad2-9f3c-1d9e38d10947', false),  -- Atlético Madrid
  ('fcbayern',         '1fc56988-9346-42a1-8035-c693f591830f', false),  -- Bayern
  ('borussiadortmund', 'eee152d3-6d31-481c-a5d3-89201cad1c41', false),  -- Dortmund
  ('psg',              '6f4750cb-c092-4eb1-a0f4-1316c84faf52', false)   -- PSG
on conflict (subreddit) do nothing;
