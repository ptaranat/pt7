# PT-7

A pocket key deck for your Mac.

<p>
  <img src="docs/light.png" width="45%" alt="PT-7, silver">
  <img src="docs/dark.png" width="45%" alt="PT-7, black, while talking">
</p>

Hold the big key to talk. Tap the rest to drive your terminal from across the room.

Made for dictating into [Handy](https://handy.computer) while using Claude Code. The look and name is a nod to Dieter Rams and Teenage Engineering's TP-7.

## how it works

Hammerspoon serves a small page to your phone over Wi-Fi. No app, no cloud.

Every button presses a real key on your Mac. The talk key holds right Command, the push-to-talk key for Handy.

The lamp lights while you talk. The display counts your take, and blinks OFFLINE if your Mac is unreachable.

## setup

1. Install [Hammerspoon](https://www.hammerspoon.org) and give it Accessibility permission.

2. In Terminal:

   ```sh
   git clone https://github.com/ptaranat/pt7.git
   ln -s "$PWD/pt7/init.lua" ~/.hammerspoon/init.lua
   ```

   Already have a Hammerspoon config? Add `dofile("/path/to/pt7/init.lua")` to it instead.

3. Reload Hammerspoon.

4. Open the Hammerspoon Console (menu bar icon, then Console). PT-7 prints your deck's private address there. Open it on your phone and Add to Home Screen.

5. Set your dictation app's push-to-talk key to right Command.

## your own keys

Edit the `KEYS` table in `init.lua`:

```lua
{ id = "escape", label = "esc", keycode = 0x35, color = "red" },
```

The `talk` role is the big key, `primary` is the tall one, the rest stack in the left column. [Keycode reference](https://eastmanreference.com/complete-list-of-applescript-key-codes).

Add `repeats = true` to a key and holding it repeats, at your Mac's key repeat rate. The arrow keys ship this way.

`ui.html` is re-read on every page load: edit, refresh, done.

## tailscale

Already on [Tailscale](https://tailscale.com)? Your deck gets end-to-end encryption and works away from home.

On your phone, drop the `.local`: with MagicDNS, `http://<your-mac-name>:8765` reaches your Mac from anywhere. Same port, same private path.

To hide the deck from your Wi-Fi entirely, bind it to your Mac's Tailscale IP (`tailscale ip -4`). In the Hammerspoon Console, run:

```lua
hs.settings.set("PT7.interface", "100.x.y.z")
```

Reload Hammerspoon. Only devices on your tailnet can reach the deck now. Undo with `hs.settings.clear("PT7.interface")`.

## fine print

The deck lives at a private address. Treat it like a key: anyone who has it can press these keys on your Mac. Traffic is unencrypted, so use a network you trust.
