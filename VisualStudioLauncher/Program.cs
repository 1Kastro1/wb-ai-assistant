using System.Diagnostics;
using System.ComponentModel;

var root = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", ".."));
var url = "http://127.0.0.1:3000";

try
{
    using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };
    using var response = await client.GetAsync(url);
    if (response.IsSuccessStatusCode)
    {
        try
        {
            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        }
        catch (Win32Exception)
        {
            Console.WriteLine($"Приложение уже работает: {url}");
        }
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

try
{
    Process.Start(new ProcessStartInfo(launcher)
    {
        WorkingDirectory = root,
        UseShellExecute = true
    });
}
catch (Win32Exception error)
{
    Console.Error.WriteLine($"Не удалось запустить приложение: {error.Message}");
    Console.Error.WriteLine($"Запустите вручную: {launcher}");
    Environment.ExitCode = 1;
}
