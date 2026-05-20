"""Atomic exposure update Lua script for Redis HASH."""

EXPOSURE_UPDATE_SCRIPT = """
local key = KEYS[1]
local total_delta = tonumber(ARGV[1])
local instr_key   = ARGV[2]
local instr_delta = tonumber(ARGV[3])
redis.call('HINCRBYFLOAT', key, 'total', total_delta)
if instr_key ~= '' then
    redis.call('HINCRBYFLOAT', key, instr_key, instr_delta)
end
return 1
"""
