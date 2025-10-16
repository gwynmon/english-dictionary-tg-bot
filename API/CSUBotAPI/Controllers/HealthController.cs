using Microsoft.AspNetCore.Mvc;
using Npgsql;

[ApiController]
[Route("health")]
public class HealthController : ControllerBase
{
    private readonly IConfiguration _config;

    public HealthController(IConfiguration config)
    {
        _config = config;
    }

    [HttpGet("db")]
    public async Task<IActionResult> CheckDbConnection()
    {
        try
        {
            var connString = _config.GetConnectionString("Postgres");
            using var conn = new NpgsqlConnection(connString);
            await conn.OpenAsync();

            // Простой запрос — безопасен и всегда работает
            using var cmd = new NpgsqlCommand("SELECT version()", conn);
            var version = await cmd.ExecuteScalarAsync() as string;

            return Ok(new { status = "ok", db = "PostgreSQL connected", version = version?.Split('\n')[0] });
        }
        catch (Exception ex)
        {
            return StatusCode(500, new { status = "error", message = ex.Message });
        }
    }
}