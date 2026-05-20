"""Atomic exposure update Lua script for Redis HASH."""

_SCRIPT_VERSION = 2

EXPOSURE_UPDATE_SCRIPT = """
local key = KEYS[1]
local total_delta = tonumber(ARGV[1])
local instr_key   = ARGV[2]
local sector_key  = ARGV[3] or ""
redis.call('HINCRBYFLOAT', key, 'total', total_delta)
if instr_key ~= '' then
    redis.call('HINCRBYFLOAT', key, instr_key, total_delta)
end
if sector_key ~= '' then
    redis.call('HINCRBYFLOAT', key, sector_key, total_delta)
end
return 1
"""
