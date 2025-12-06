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
        _logger.LogInformation("Adding word '{Word}' for user {UserId} in theme '{Theme}'", 
            request.Word, request.UserId, request.Theme);

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

            // 2. Получить или создать тему
            int themeId;
            await using (var cmd = new NpgsqlCommand(@"
                INSERT INTO themes (user_id, name) 
                VALUES (@UserId, @ThemeName) 
                ON CONFLICT (user_id, name) DO NOTHING;
                
                SELECT id FROM themes WHERE user_id = @UserId AND name = @ThemeName;", conn, tx))
            {
                cmd.Parameters.AddWithValue("UserId", request.UserId);
                cmd.Parameters.AddWithValue("ThemeName", request.Theme);
                themeId = (int)(await cmd.ExecuteScalarAsync()!);
            }

            // 3. Добавить слово
            await using (var cmd = new NpgsqlCommand(@"
                INSERT INTO words (theme_id, word, translation, definition) 
                VALUES (@ThemeId, @Word, @Translation, @Definition)
                ON CONFLICT (theme_id, word) DO NOTHING;", conn, tx))
            {
                cmd.Parameters.AddWithValue("ThemeId", themeId);
                cmd.Parameters.AddWithValue("Word", request.Word);
                cmd.Parameters.AddWithValue("Translation", request.Translation ?? (object)DBNull.Value);
                cmd.Parameters.AddWithValue("Definition", request.Definition ?? (object)DBNull.Value);
                await cmd.ExecuteNonQueryAsync();
            }

            await tx.CommitAsync();

            var cacheKey = $"user:{request.UserId}:theme:{request.Theme}";
            await _redis.GetDatabase().KeyDeleteAsync(cacheKey);
        }
        catch
        {
            await tx.RollbackAsync();
            throw;
        }

        _logger.LogInformation("Word '{Word}' added successfully", request.Word);
    }

    public async Task<List<WordResponse>> GetWordsAsync(long userId, string theme)
    {
        var cacheKey = $"user:{userId}:theme:{theme}";
        var db = _redis.GetDatabase();

        // 1. Попробуем взять из кэша
        //var cached = await db.StringGetAsync(cacheKey);
        //if (cached.HasValue)
        //{
        //    _logger.LogInformation("Cache hit for user {UserId}, theme '{Theme}'", userId, theme);
        //    var words = JsonSerializer.Deserialize<List<WordResponse>>(cached!, 
        //        new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase });
        //    return words!;
        //}

        // 2. Читаем и обновляем в одной транзакции
        await using var conn = new NpgsqlConnection(_connectionString);
        await conn.OpenAsync();
        await using var tx = await conn.BeginTransactionAsync();

        try
        {
            // Получаем ID и данные слов
            var words = new List<(int Id, WordResponse Response)>();
            await using (var cmd = new NpgsqlCommand(@"
                SELECT w.id, w.word, w.translation, w.definition
                FROM words w
                JOIN themes t ON w.theme_id = t.id
                WHERE t.user_id = @UserId AND t.name = @ThemeName
                ORDER BY w.created_at;", conn, tx))
            {
                cmd.Parameters.AddWithValue("UserId", userId);
                cmd.Parameters.AddWithValue("ThemeName", theme);

                await using var reader = await cmd.ExecuteReaderAsync();
                while (await reader.ReadAsync())
                {
                    words.Add((
                        reader.GetInt32("id"),
                        new WordResponse
                        {
                            Word = reader.GetString("word"),
                            Translation = reader.IsDBNull("translation") ? null : reader.GetString("translation"),
                            Definition = reader.IsDBNull("definition") ? null : reader.GetString("definition")
                        }
                    ));
                }
            }

            // Увеличиваем access_count для всех выбранных слов
            if (words.Count > 0)
            {
                var ids = string.Join(",", words.Select(w => w.Id));
                await using var updateCmd = new NpgsqlCommand($@"
                    UPDATE words 
                    SET access_count = access_count + 1 
                    WHERE id IN ({ids});", conn, tx);
                await updateCmd.ExecuteNonQueryAsync();
            }

            await tx.CommitAsync();

            // Кэшируем (уже после увеличения счётчика)
            var wordResponses = words.Select(w => w.Response).ToList();
            var json = JsonSerializer.Serialize(wordResponses,
                new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase });
            await db.StringSetAsync(cacheKey, json, TimeSpan.FromMinutes(10));

            _logger.LogInformation("Loaded and incremented access_count for {Count} words", words.Count);
            return wordResponses;
        }
        catch
        {
            await tx.RollbackAsync();
            throw;
        }
    }
}