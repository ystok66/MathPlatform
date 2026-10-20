using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class StartMathPlatform
{
    private static bool Run(string launcher, string root, string command, int timeout)
    {
        var info = new ProcessStartInfo(launcher, command);
        info.WorkingDirectory = root;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        using (var process = new Process())
        {
            process.StartInfo = info;
            // Drain both pipes to prevent launcher output from blocking exit.
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { };
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            if (!process.WaitForExit(timeout))
            {
                // Only terminate this short-lived launcher, never service processes.
                process.Kill();
                throw new TimeoutException("The launcher did not return in time. Use check.cmd to inspect service status.");
            }
            return process.ExitCode == 0;
        }
    }

    [STAThread]
    private static int Main(string[] args)
    {
        bool noBrowser = args.Length == 1 && args[0] == "--no-browser";
        if (args.Length > 0 && !noBrowser) return 2;
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string launcher = Path.Combine(root, "launcher", "MathPlatform.exe");
            if (!File.Exists(launcher))
                throw new FileNotFoundException("Extract the entire math-platform folder before starting. Do not copy this EXE alone.");
            if (!Run(launcher, root, "start", 15000))
                throw new InvalidOperationException("The local services could not start. Run check.cmd in the same folder for details.");
            if (!noBrowser && !Run(launcher, root, "open", 5000))
                throw new InvalidOperationException("Services started, but the browser could not open. Run check.cmd to find the local URL.");
            return 0;
        }
        catch (Exception error)
        {
            if (!noBrowser)
                MessageBox.Show(error.Message, "Math Platform", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
