-- Migration: add order_side and exit_type columns to orders table
-- Run once in Supabase SQL editor
-- All existing rows are BUY entries; SELL orders were blocked by this missing column.

ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS order_side text
    NOT NULL DEFAULT 'BUY'
    CHECK (order_side IN ('BUY', 'SELL'));

ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS exit_type text
    CHECK (exit_type IN ('EXIT_CLOSE', 'EXIT_TRIM'));
