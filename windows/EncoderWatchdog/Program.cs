using System.Diagnostics;
using System.Text.Json;

record WatchdogConfig(
    string Pythonw,
    string EncoderScript,
    string Heartbeat,
    string PidFile,
    int MaxHeartbeatAgeSeconds = 120,
    int StartupTimeoutSeconds = 30
);

static class Program
{
    private static readonly string Root = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
        "OrthrusMediaEncoder"
    );
    private static readonly string ConfigPath = Path.Combine(Root, "watchdog.json");
    private static readonly string LogPath = Path.Combine(Root, "watchdog.log");

    [STAThread]
    public static int Main()
    {
        try
        {
            Directory.CreateDirectory(Root);
            var config = JsonSerializer.Deserialize<WatchdogConfig>(
                File.ReadAllText(ConfigPath),
                new JsonSerializerOptions { PropertyNameCaseInsensitive = true }
            ) ?? throw new InvalidOperationException("Watchdog configuration is empty");

            if (!File.Exists(config.Pythonw))
                throw new FileNotFoundException("pythonw.exe is missing", config.Pythonw);
            if (!File.Exists(config.EncoderScript))
                throw new FileNotFoundException("Desktop encoder script is missing", config.EncoderScript);

            var heartbeatFresh = File.Exists(config.Heartbeat)
                && DateTime.UtcNow - File.GetLastWriteTimeUtc(config.Heartbeat)
                    <= TimeSpan.FromSeconds(config.MaxHeartbeatAgeSeconds);
            var worker = ReadWorker(config.PidFile);
            if (heartbeatFresh)
            {
                Log(worker is { HasExited: false }
                    ? $"healthy pid={worker.Id}"
                    : "healthy heartbeat; pid unavailable");
                return 0;
            }

            if (worker is { HasExited: false })
            {
                Log($"stale heartbeat; terminating pid={worker.Id}");
                worker.Kill(entireProcessTree: true);
                worker.WaitForExit(10_000);
            }

            var startedAt = DateTime.UtcNow;
            var launched = Process.Start(new ProcessStartInfo
            {
                FileName = config.Pythonw,
                Arguments = $"\"{config.EncoderScript}\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            }) ?? throw new InvalidOperationException("Windows did not create the encoder process");

            var deadline = DateTime.UtcNow.AddSeconds(config.StartupTimeoutSeconds);
            while (DateTime.UtcNow < deadline)
            {
                if (File.Exists(config.Heartbeat)
                    && File.GetLastWriteTimeUtc(config.Heartbeat) >= startedAt.AddSeconds(-1))
                {
                    Log($"started pid={launched.Id}");
                    return 0;
                }
                if (launched.HasExited)
                    throw new InvalidOperationException(
                        $"Encoder exited during startup with code {launched.ExitCode}"
                    );
                Thread.Sleep(1_000);
            }

            throw new TimeoutException("Encoder did not publish a heartbeat after launch");
        }
        catch (Exception error)
        {
            Log("error: " + error);
            return 1;
        }
    }

    private static Process? ReadWorker(string pidFile)
    {
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(pidFile));
            var pid = document.RootElement.GetProperty("pid").GetInt32();
            var process = Process.GetProcessById(pid);
            return process.ProcessName.StartsWith("python", StringComparison.OrdinalIgnoreCase)
                ? process
                : null;
        }
        catch
        {
            return null;
        }
    }

    private static void Log(string message)
    {
        try
        {
            File.AppendAllText(LogPath, $"{DateTime.Now:O} {message}{Environment.NewLine}");
        }
        catch
        {
            // A logging failure must not crash the background watchdog.
        }
    }
}
