-- PT-7: a phone-sized key deck for the Mac it runs on. LAN-only, no dependencies.
-- The talk key latches right-command for push-to-talk dictation apps like Handy.

local PORT = 8765

-- Optional: bind to a single address, e.g. your Tailscale IP ("100.x.y.z")
-- to make the deck reachable only over the tailnet. Unset listens everywhere.
-- Set from the Hammerspoon console: hs.settings.set("PT7.interface", "100.x.y.z")
local INTERFACE = hs.settings.get("PT7.interface")

local masks = hs.eventtap.event.rawFlagMasks
local RIGHT_CMD_FLAGS = masks.command + (masks.deviceRightCommand or 0x10)

-- The phone lays keys out by role: "talk" is the big hold key on top, "primary"
-- fills the tall right slot, everything else stacks in the left column.
-- color: "red" | "green" | nil.
local KEYS = {
  { id = "talk",   label = "talk", keycode = 0x36, role = "talk", flags = RIGHT_CMD_FLAGS },
  { id = "up",     label = "up",    keycode = 0x7E, repeats = true },
  { id = "down",   label = "down",  keycode = 0x7D, repeats = true },
  { id = "tab",    label = "tab",   keycode = 0x30 },
  { id = "escape", label = "esc",   keycode = 0x35, color = "red" },
  { id = "enter",  label = "enter", keycode = 0x24, role = "primary", color = "green" },
}

local byId = {}
for _, key in ipairs(KEYS) do byId[key.id] = key end

local KEYCODE_PROP = hs.eventtap.event.properties.keyboardEventKeycode
local AUTOREPEAT_PROP = hs.eventtap.event.properties.keyboardEventAutorepeat

local function press(key, isDown, isRepeat)
  local e = hs.eventtap.event.newKeyEvent({}, key.keycode, isDown)
  e:setProperty(KEYCODE_PROP, key.keycode) -- Handy reads raw keycodes
  if isRepeat then e:setProperty(AUTOREPEAT_PROP, 1) end
  e:rawFlags(isDown and (key.flags or 0) or 0)
  e:post()
  if not isRepeat then
    print(string.format("[PT-7] %s %s", key.id, isDown and "down" or "up"))
  end
end

-- Synthetic key events never auto-repeat, so held "repeats" keys are re-posted.
local repeating = {}

local function stopRepeat(key)
  local t = repeating[key.id]
  if t then t:stop(); repeating[key.id] = nil end
end

local function startRepeat(key)
  if repeating[key.id] then return end
  repeating[key.id] = hs.timer.doAfter(hs.eventtap.keyRepeatDelay(), function()
    repeating[key.id] = hs.timer.doEvery(hs.eventtap.keyRepeatInterval(), function()
      press(key, true, true)
    end)
    press(key, true, true)
  end)
end

-- Resolve through the ~/.hammerspoon symlink so ui.html loads from the repo.
local DIR = hs.fs.pathToAbsolute(debug.getinfo(1, "S").source:sub(2)):match("(.*/)")

local function page()
  local f = assert(io.open(DIR .. "ui.html"))
  local html = f:read("a")
  f:close()
  return (html:gsub("__KEYS__", function() return hs.json.encode(KEYS) end, 1))
end

local function iconPNG()
  -- Mirrors the talk key: wordmark top left, recessed LED bottom left.
  local c = hs.canvas.new({ x = 0, y = 0, w = 180, h = 180 })
  c:appendElements(
    { type = "rectangle", action = "fill", fillColor = { hex = "#202126" } },
    { type = "text", frame = { x = 22, y = 14, w = 150, h = 60 }, text = "PT\u{2013}7",
      textColor = { hex = "#ececea" }, textSize = 42, textFont = "HelveticaNeue-Thin" },
    { type = "circle", action = "fill", center = { x = 40, y = 136 }, radius = 20,
      fillColor = { hex = "#0d0e10" } },
    { type = "circle", action = "fill", center = { x = 40, y = 136 }, radius = 17,
      fillColor = { hex = "#f0531c" } },
    { type = "circle", action = "fill", center = { x = 35, y = 130 }, radius = 10,
      fillColor = { hex = "#ffffff", alpha = 0.10 } },
    { type = "circle", action = "fill", center = { x = 34, y = 129 }, radius = 6,
      fillColor = { hex = "#ffffff", alpha = 0.18 } }
  )
  local dataURL = c:imageFromCanvas():encodeAsURLString(false, "PNG")
  return hs.base64.decode(dataURL:match("base64,(.*)"))
end
local iconOK, ICON = pcall(iconPNG)

-- The deck lives under a private random path; requests without it get 404s.
local TOKEN = hs.settings.get("PT7.token")
if not TOKEN then
  TOKEN = hs.host.globallyUniqueString():gsub("%-", ""):sub(1, 8):lower()
  hs.settings.set("PT7.token", TOKEN)
end

local TEXT = { ["Content-Type"] = "text/plain" }

-- Global on purpose: Hammerspoon garbage-collects an unreferenced httpserver,
-- which silently kills the listener.
pt7Server = hs.httpserver.new(false, false)
pt7Server:setPort(PORT)
if INTERFACE then pt7Server:setInterface(INTERFACE) end
pt7Server:setCallback(function(method, path)
  local prefix = "/" .. TOKEN
  if path:sub(1, #prefix) ~= prefix then return "not found", 404, TEXT end
  local sub = path:sub(#prefix + 1)
  if sub == "" then return "", 302, { Location = prefix .. "/" } end
  if method == "GET" then
    if sub == "/" then
      return page(), 200, { ["Content-Type"] = "text/html; charset=utf-8" }
    elseif sub == "/ping" then
      return "ok", 200, TEXT
    elseif sub == "/icon.png" and iconOK then
      return ICON, 200, { ["Content-Type"] = "image/png" }
    end
  elseif method == "POST" then
    local id, action = sub:match("^/(%w+)/(%a+)$")
    local key = byId[id]
    if key and (action == "down" or action == "up") then
      if action == "down" then
        press(key, true)
        if key.repeats then startRepeat(key) end
      else
        stopRepeat(key)
        press(key, false)
      end
      return "ok", 200, TEXT
    end
  end
  return "not found", 404, TEXT
end)
pt7Server:start()

local host = hs.execute("hostname -s"):gsub("%s+$", "")
print(string.format("[PT-7] deck at http://%s.local:%d/%s/", host, PORT, TOKEN))

-- Release everything on reload/quit so no key ever sticks.
hs.shutdownCallback = function()
  for _, key in ipairs(KEYS) do
    stopRepeat(key)
    press(key, false)
  end
end
