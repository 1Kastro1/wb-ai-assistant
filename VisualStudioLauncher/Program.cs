using System.Diagnostics;

var root = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", ".."));
var url = "http://127.0.0.1:3000";

try
{
    using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };
    using var response = await client.GetAsync(url);
    if (response.IsSuccessStatusCode)
    {
        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        return;
    }
}
catch (HttpRequestException) { }
catch (TaskCanceledException) { }

var launcher = Path.Combine(root, "start.bat");
if (!File.Exists(launcher))
{
    Console.Error.WriteLine($"Не найден файл запуска: {launcher}");
    Environment.ExitCode = 1;
    return;
}

Process.Start(new ProcessStartInfo(launcher)
{
    WorkingDirectory = root,
    UseShellExecute = true
});
