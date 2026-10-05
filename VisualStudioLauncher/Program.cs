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

var launcher = Path.Combine(root, "scripts", "manage.ps1");
if (!File.Exists(launcher))
{
    Console.Error.WriteLine($"Не найден файл запуска: {launcher}");
    Environment.ExitCode = 1;
    return;
}

try
{
    using var process = Process.Start(new ProcessStartInfo("powershell.exe")
    {
        ArgumentList = { "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", launcher, "-Action", "start" },
        WorkingDirectory = root,
        UseShellExecute = false,
        CreateNoWindow = true
    }) ?? throw new Win32Exception("Не удалось создать процесс запуска.");
    await process.WaitForExitAsync();
    if (process.ExitCode != 0)
    {
        Environment.ExitCode = process.ExitCode;
        return;
    }
    using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
    using var response = await client.GetAsync(url);
    response.EnsureSuccessStatusCode();
    Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
}
catch (Exception error) when (error is Win32Exception or HttpRequestException or TaskCanceledException)
{
    Console.Error.WriteLine($"Не удалось запустить приложение: {error.Message}");
    Console.Error.WriteLine($"Проверьте запуск: {launcher}");
    Environment.ExitCode = 1;
}
