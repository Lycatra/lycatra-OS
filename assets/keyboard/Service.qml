import QtQuick
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons
import qs.Ui

Item {
  id: root

  property string layout: ""
  property var layoutsByKeyboard: Object.create(null)

  function isTypingKeyboard(name) {
    return name && !/^(hl-virtual-keyboard|fcitx|virtual|power-button|sleep-button|lid-switch|video-bus)|(-extra-buttons|-wmi-hotkeys|-wireless-radio-control|\(avrcp\))(-\d+)?$/i.test(name)
  }
  readonly property string label: layout === "English (US)" ? strings.t("layout.us")
    : layout === "German (Switzerland)" ? strings.t("layout.ch") : layout

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  Component.onCompleted: keyboardSnapshot.running = true

  Process {
    id: keyboardSnapshot
    command: ["hyprctl", "-j", "devices"]

    stdout: StdioCollector {
      onStreamFinished: {
        try {
          const keyboards = JSON.parse(text).keyboards
          for (const keyboard of keyboards) {
            if (root.isTypingKeyboard(keyboard.name) && keyboard.active_keymap
              && root.layoutsByKeyboard[keyboard.name] === undefined)
              root.layoutsByKeyboard[keyboard.name] = keyboard.active_keymap
          }
        } catch (error) {}
      }
    }
  }

  Connections {
    target: InputDevices
    function onChanged() { keyboardSnapshot.running = true }
  }

  Connections {
    target: Hyprland
    function onRawEvent(event) {
      if (event.name !== "activelayout") return
      const data = String(event.data || "")
      const comma = data.indexOf(",")
      if (comma === -1) return
      const keyboard = data.slice(0, comma)
      const next = data.slice(comma + 1).trim()
      if (!root.isTypingKeyboard(keyboard) || !next || next.length > 100) return

      const previous = root.layoutsByKeyboard[keyboard]
      root.layoutsByKeyboard[keyboard] = next
      if (previous === undefined || previous === next || (popup.shown && next === root.layout)) return
      root.layout = next
      popup.shown = true
      hide.restart()
    }
  }

  ShellIpc {
    target: "local.keyboard-layout-popup"
    function status(): string {
      return JSON.stringify({layout: root.label, visible: popup.shown})
    }
  }

  Timer {
    id: hide
    interval: 950
    onTriggered: popup.shown = false
  }

  OverlayWindow {
    id: popup
    shownKeyboardFocus: WlrKeyboardFocus.None
    WlrLayershell.namespace: "omarchy-keyboard-layout-popup"
    mask: Region {}

    BorderSurface {
      id: card
      anchors.horizontalCenter: parent.horizontalCenter
      y: Math.round(parent.height / 3 - height / 2)
      width: Math.max(Style.space(154), label.implicitWidth + Style.space(38))
      height: label.implicitHeight + Style.space(24)
      color: Util.alpha(Commons.Color.background, 0.97)
      borderSpec: Border.surfaceSpec("popups", "border", Commons.Color.popups.border, Math.max(1, Style.space(2)))
      radius: Style.cornerRadius

      Text {
        id: label
        anchors.centerIn: parent
        text: root.label
        textFormat: Text.PlainText
        color: Commons.Color.popups.text
        font.family: Style.font.family
        font.pixelSize: Style.font.title
        font.bold: true
      }
    }
  }
}
