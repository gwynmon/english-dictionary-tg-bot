using Npgsql;
using CSUBotAPI.DTOs;
using StackExchange.Redis;
using System.Text.Json;
using System.Data;
using Microsoft.Extensions.Logging;


namespace CSUBotAPI.Services;

public class WordService
{
    private readonly string _connectionString;
    private readonly IConnectionMultiplexer _redis;
    private readonly ILogger<WordService> _logger;

    public WordService(IConfiguration config, IConnectionMultiplexer redis, ILogger<WordService> logger)
    {
        _connectionString = config.GetConnectionString("Postgres") 
            ?? throw new InvalidOperationException("Postgres connection string missing");
        _redis = redis;
        _logger = logger;
    }

    public async Task AddWordAsync(AddWordRequest request)
    {
        _logger.LogInformation("Adding word '{Word}' for user {UserId}", 
            request.Word, request.UserId);

        await using var conn = new NpgsqlConnection(_connectionString);
        await conn.OpenAsync();
        await using var tx = await conn.BeginTransactionAsync();

        try
        {
            // 1. Создать пользователя, если не существует
            await using (var cmd = new NpgsqlCommand(@"
                INSERT INTO users (telegram_id) 
                VALUES (@UserId) 
                ON CONFLICT (telegram_id) DO NOTHING;", conn, tx))
            {
                cmd.Parameters.AddWithValue("UserId", request.UserId);
                await cmd.ExecuteNonQueryAsync();
            }

            // 2. Добавить слово напрямую (без темы)
            await using (var cmd = new NpgsqlCommand(@"
                INSERT INTO words (user_id, word, translation, definition) 
                VALUES (@UserId, @Word, @Translation, @Definition)
                ON CONFLICT (user_id, word) DO NOTHING;", conn, tx))
            {
                cmd.Parameters.AddWithValue("UserId", request.UserId);
                cmd.Parameters.AddWithValue("Word", request.Word);
                cmd.Parameters.AddWithValue("Translation", request.Translation ?? (object)DBNull.Value);
                cmd.Parameters.AddWithValue("Definition", request.Definition ?? (object)DBNull.Value);
                await cmd.ExecuteNonQueryAsync();
            }

            await tx.CommitAsync();

            // Инвалидация кэша (если используешь кэширование в GetWordsAsync)
            var cacheKey = $"user:{request.UserId}:words";
            await _redis.GetDatabase().KeyDeleteAsync(cacheKey);
        }
        catch
        {
            await tx.RollbackAsync();
            throw;
        }

        _logger.LogInformation("Word '{Word}' added successfully", request.Word);
    }

    public async Task<(List<WordResponse> Words, int TotalAccessCount)>  GetWordsAsync(long userId)
    {
            // Всегда читаем из БД и инкрементируем — кэш отключён для этого эндпоинта
        await using var conn = new NpgsqlConnection(_connectionString);
        await conn.OpenAsync();
        await using var tx = await conn.BeginTransactionAsync();

        try
        {
            // 1. Получаем слова и одновременно увеличиваем access_count
            var words = new List<WordResponse>();
            await using (var cmd = new NpgsqlCommand(@"
                -- Сначала обновляем счётчики
                UPDATE words 
                SET access_count = access_count + 1 
                WHERE user_id = @UserId;

                -- Затем читаем обновлённые данные
                SELECT word, translation, definition, access_count
                FROM words
                WHERE user_id = @UserId
                ORDER BY created_at;", conn, tx))
            {
                cmd.Parameters.AddWithValue("UserId", userId);
                await using var reader = await cmd.ExecuteReaderAsync();

                while (await reader.ReadAsync())
                {
                    words.Add(new WordResponse
                    {
                        Word = reader.GetString("word"),
                        Translation = reader.IsDBNull("translation") ? null : reader.GetString("translation"),
                        Definition = reader.IsDBNull("definition") ? null : reader.GetString("definition"),
                        AccessCount = reader.GetInt32("access_count") // ← возвращаем счётчик
                    });
                }
            }

            await tx.CommitAsync();

            // Считаем общий access_count по всем словам пользователя (опционально)
            int totalAccessCount = words.Sum(w => w.AccessCount);

            return (words, totalAccessCount);
        }
        catch
        {
            await tx.RollbackAsync();
            throw;
        }
    }
}