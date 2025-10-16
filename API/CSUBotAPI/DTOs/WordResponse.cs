namespace CSUBotAPI.DTOs;

public class WordResponse
{
    public string Word { get; set; } = string.Empty;
    public string? Translation { get; set; }
    public string? Definition { get; set; }
}