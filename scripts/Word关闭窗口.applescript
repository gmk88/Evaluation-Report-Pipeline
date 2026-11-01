-- 只关闭标题含 winKey 的 Word 窗口；先确认最前面的窗口就是它再按 Cmd+W。
-- 出现“是否存储”对话框时不替用户选择，只返回按钮名，由调用者决定。
on run argv
	set winKey to item 1 of argv
	tell application "Microsoft Word" to activate
	delay 0.5
	tell application "System Events"
		tell process "Microsoft Word"
			set target to missing value
			repeat with w in windows
				if name of w contains winKey then set target to w
			end repeat
			if target is missing value then return "not open"
			perform action "AXRaise" of target
			delay 0.6
			set fw to name of window 1
			if fw does not contain winKey then return "front window is not target: " & fw
			keystroke "w" using command down
			delay 1.5
			repeat with w in windows
				if name of w contains winKey then
					if exists sheet 1 of w then return "save sheet: " & ((name of every button of sheet 1 of w) as string)
					return "still open"
				end if
			end repeat
			return "closed"
		end tell
	end tell
end run
