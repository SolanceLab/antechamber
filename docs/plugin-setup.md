# Setting up the plugin

[← back to the README](../README.md)

Then ask anything. The first time, the assistant asks where you keep notes and whether you have Claude or ChatGPT chat exports, looks at what is there, and sets Antechamber up for you (it offers an Obsidian vault or a memories folder only if you have one): no service to start, no key, no settings file to write. Your folders stay on your computer. Setup works once: after that the assistant cannot change which folders are searched, and later changes are made by editing the plugin's settings file (`~/.config/antechamber/plugin.toml`). The plugin keeps its own file, so it never picks up or overwrites a service settings file (`config.toml`) on its own (only an `ANTECHAMBER_CONFIG` you set yourself points it elsewhere), and service-only settings (`[shelves] adapters`, `[service] extensions`) are ignored in the plugin. Only folders inside your home folder are accepted, never the home folder itself or private ones such as `.ssh` or `.config`.

The code map is an advanced add-on, set up by hand (see [Settings](settings.md)). This is for assistants that run on your own computer. claude.ai cannot run local programs: use the [remote door](run-it-yourself.md#use-it-from-claudeai-or-chatgpt-remote-door).
