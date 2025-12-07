namespace CSUBotAPI.DTOs;

public class AddWordRequest
{
    public long UserId { get; set; }
    public string Word { get; set; } = string.Empty;
    public string? Translation { get; set; }
    public string? Definition { get; set; }
}