using Microsoft.AspNetCore.Mvc;
using CSUBotAPI.DTOs;
using CSUBotAPI.Services;

namespace CSUBotAPI.Controllers;

[ApiController]
[Route("api/v1/[controller]")]
public class WordsController : ControllerBase
{
    private readonly WordService _wordService;

    public WordsController(WordService wordService)
    {
        _wordService = wordService;
    }

    [HttpPost]
    public async Task<IActionResult> AddWord([FromBody] AddWordRequest request)
    {
        if (string.IsNullOrWhiteSpace(request.Theme) || string.IsNullOrWhiteSpace(request.Word))
        {
            return BadRequest("Theme and Word are required.");
        }

        await _wordService.AddWordAsync(request);
        return Created(); // 201 Created
    }

    [HttpGet]
    public async Task<IActionResult> GetWords([FromQuery] long userId, [FromQuery] string theme)
    {
        if (string.IsNullOrWhiteSpace(theme))
            return BadRequest("Theme is required.");

        var words = await _wordService.GetWordsAsync(userId, theme);
        return Ok(new { theme, words });
    }
}