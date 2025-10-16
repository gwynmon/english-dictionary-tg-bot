using Npgsql;
using Microsoft.Extensions.Configuration;

namespace CSUBotAPI.Services;

public static class DatabaseInitializer
{
    public static async Task InitializeAsync(IConfiguration config)
    {
        var connString = config.GetConnectionString("Postgres");
        using var conn = new NpgsqlConnection(connString);
        await conn.OpenAsync();

        var sql = await File.ReadAllTextAsync("DbScripts/init.sql");
        using var cmd = new NpgsqlCommand(sql, conn);
        await cmd.ExecuteNonQueryAsync();
    }
}