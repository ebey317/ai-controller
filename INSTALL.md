# AI Controller — Installation Guide

For complete installation instructions, see the **[Quick Start](README.md#quick-start)** section in the main README.

## Supported Platforms

| Platform | Package Manager | Notes |
|----------|-----------------|-------|
| **Linux** | apt | Ubuntu, Mint, Debian |
| **macOS** | Homebrew | `brew install` required |
| **Windows** | WSL2 | Windows Subsystem for Linux |

## Quick Install

```bash
git clone https://github.com/ebey317/-AI-controller..git
cd '-AI-controller.'
bash install.sh
```

## What the Installer Does

1. **Installs system dependencies** (Python, GTK3, Cairo, AntiMicroX, etc.)
2. **Creates a virtual environment** with all Python packages
3. **Installs systemd user services** (voice bridge, PTT, keyboard, legend, profile loader)
4. **Prompts for your Groq API key** (stored securely in your keychain)
5. **Enables auto-start** on login

## Post-Install

1. Plug in your controller (USB or Bluetooth)
2. Put on headphones
3. Press **Right Trigger** and talk
4. Press **View** button to toggle the floating keyboard

## Uninstall

```bash
bash uninstall.sh
```

This removes all services, files, and configurations.

---

**Full documentation:** [README.md](README.md)
