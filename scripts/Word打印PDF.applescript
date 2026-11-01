-- 用法：osascript Word打印PDF.applescript "<Word窗口名包含的文字>"
-- 通过 Microsoft Word 原生打印对话框“存储为PDF”导出（Word 未激活时 save as PDF 不可用；不用 LibreOffice）。
-- PDF 存到存储面板当前位置（上次使用的文件夹），文件名同 docx；由调用方找到后移到目标目录。
-- 判断是否成功看 PDF 文件是否生成、大小是否稳定，不看本脚本的返回值。
-- 打印对话框已打开时直接接着用；“PDF”菜单按钮要等预览加载完才出现，最多等 60 秒；
-- 长文档打开后 Word 还在分页时，“打印”窗口可能先出现又被替换，所以每次重新取窗口。
on findMenuButton()
	tell application "System Events"
		tell process "Microsoft Word"
			try
				if not (exists window "打印") then return missing value
				set items_ to entire contents of window "打印"
			on error
				return missing value
			end try
			repeat with x in items_
				try
					if role of x is "AXMenuButton" then return contents of x
				end try
			end repeat
		end tell
	end tell
	return missing value
end findMenuButton

on run argv
	set winKey to item 1 of argv
	tell application "Microsoft Word" to activate
	delay 1
	tell application "System Events"
		tell process "Microsoft Word"
			set frontmost to true
			if not (exists window "打印") then
				set w to (first window whose name contains winKey)
				perform action "AXRaise" of w
				delay 1
				keystroke "p" using command down
				repeat 30 times
					if exists window "打印" then exit repeat
					delay 1
				end repeat
			end if
		end tell
	end tell
	set mb to missing value
	repeat 60 times
		set mb to my findMenuButton()
		if mb is not missing value then exit repeat
		delay 1
	end repeat
	if mb is missing value then return "error: PDF menu button not found"
	try
		tell application "System Events"
			tell process "Microsoft Word"
				click mb
				delay 1.5
				click menu item "存储为PDF…" of menu 1 of mb
				repeat 20 times
					if (count of sheets of window "打印") > 0 then exit repeat
					delay 1
				end repeat
				delay 1
				perform action "AXPress" of button "存储" of sheet 1 of window "打印"
			end tell
		end tell
	on error e
		return "error: " & e
	end try
	delay 2
	try
		tell application "System Events"
			tell process "Microsoft Word"
				if exists sheet 1 of sheet 1 of window "打印" then
					perform action "AXPress" of button "替换" of sheet 1 of sheet 1 of window "打印"
				end if
			end tell
		end tell
	end try
	return "submitted"
end run
