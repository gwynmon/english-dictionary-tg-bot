using System.Text.Json;
using dotenv.net;
using Serilog;
using CSUBotAPI.Middleware;
using CSUBotAPI.Services;
using CSUBotAPI.HealthChecks;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using StackExchange.Redis;

DotEnv.Load();

Log.Logger = new LoggerConfiguration()
    .WriteTo.Console()
    .CreateBootstrapLogger();

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddControllers();

var postgresHost = Environment.GetEnvironmentVariable("POSTGRES_HOST") ?? "localhost";
var postgresPort = Environment.GetEnvironmentVariable("POSTGRES_PORT") ?? "5432";
var postgresDb = Environment.GetEnvironmentVariable("POSTGRES_DB") ?? "CSUBotDB";
var postgresUser = Environment.GetEnvironmentVariable("POSTGRES_USER") ?? "CSUBotDB_user";
var postgresPass = Environment.GetEnvironmentVariable("POSTGRES_PASSWORD") ?? "dev_password";

var connString = $"Host={postgresHost};Port={postgresPort};Database={postgresDb};Username={postgresUser};Password={postgresPass}";

builder.Configuration["ConnectionStrings:Postgres"] = connString;
builder.Configuration["Redis:ConnectionString"] = 
    Environment.GetEnvironmentVariable("REDIS_CONNECTION_STRING") ?? "localhost:6379";
builder.Configuration["Redis:Password"] = 
    Environment.GetEnvironmentVariable("REDIS_PASSWORD") ?? "";
builder.Configuration["ApiSecurity:BotApiKey"] = 
    Environment.GetEnvironmentVariable("BOT_API_KEY") ?? "dev-api-key";

builder.Host.UseSerilog((ctx, logger) =>
    {
        logger.WriteTo.Console();
    });

builder.Services.AddOpenApi();

builder.Host.UseSerilog((ctx, logger) => logger.WriteTo.Console());

builder.Services.AddScoped<WordService>();

builder.Services.AddSingleton<IConnectionMultiplexer>(sp =>
{
    var config = sp.GetRequiredService<IConfiguration>();
    var connString = config["Redis:ConnectionString"]!;
    var password = config["Redis:Password"];
    var options = ConfigurationOptions.Parse(connString);
    if (!string.IsNullOrEmpty(password))
        options.Password = password;
    return ConnectionMultiplexer.Connect(options);
});

// После AddHealthChecks()
builder.Services.AddHealthChecks()
    .AddNpgSql(builder.Configuration.GetConnectionString("Postgres")!)
    .Add(new HealthCheckRegistration(
        "redis", 
        sp => new RedisHealthCheck(sp.GetRequiredService<IConnectionMultiplexer>()),
        failureStatus: HealthStatus.Unhealthy,
        tags: new[] { "ready" }));

var app = builder.Build();

try
{
    await DatabaseInitializer.InitializeAsync(builder.Configuration);
    Log.Information("Database initialized successfully.");
}
catch (Exception ex)
{
    Log.Fatal(ex, "Failed to initialize database.");
    return;
}

if (app.Environment.IsDevelopment())
{
    app.MapOpenApi();
}

//app.UseHttpsRedirection();

app.UseMiddleware<ApiKeyMiddleware>();
app.MapControllers();

app.MapGet("/debug/config", (IConfiguration config) =>
{
    var hasKey = !string.IsNullOrEmpty(config["ApiSecurity:BotApiKey"]);
    var dbSet = !string.IsNullOrEmpty(config.GetConnectionString("Postgres"));
    return new { Redis = config["Redis:ConnectionString"], ApiKeySet = hasKey, DbConfigured = dbSet };
});

app.Urls.Add("http://localhost:5000");

app.MapHealthChecks("/health/ready");
app.MapHealthChecks("/health/live");

app.Run();
