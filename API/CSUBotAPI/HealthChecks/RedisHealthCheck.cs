using Microsoft.Extensions.Diagnostics.HealthChecks;
using StackExchange.Redis;

namespace CSUBotAPI.HealthChecks;

public class RedisHealthCheck : IHealthCheck
{
    private readonly IConnectionMultiplexer _redis;

    public RedisHealthCheck(IConnectionMultiplexer redis)
    {
        _redis = redis;
    }

    public async Task<HealthCheckResult> CheckHealthAsync(
        HealthCheckContext context, 
        CancellationToken cancellationToken = default)
    {
        try
        {
            var db = _redis.GetDatabase();
            
            // Просто вызываем PingAsync без cancellationToken
            await db.PingAsync();
            
            return HealthCheckResult.Healthy("Redis is up and responding to PING");
        }
        catch (Exception ex)
        {
            return HealthCheckResult.Unhealthy($"Redis is unreachable: {ex.Message}", ex);
        }
    }
}