-- 桌面小程序“预评价编制工作台”（Mac）的程序本体。make_mac_app.sh 把它编译成常驻的 .app（osacompile -s），
-- 不显示菜单栏和程序坞图标（LSUIElement）。
-- 双击：运行流水线里的 app/desktop/mac_launch.sh，它在后台起网页服务、打开浏览器。
-- 服务是这次起的，小程序就留在后台陪着，服务停了才退出：服务读桌面上的文件时，系统按“谁起的服务”认权限，
-- 起服务的程序退出以后还认不认，没有把握。服务不是它起的（早就在跑，或者转给了命令窗口），打开网页就退出。
-- 小程序在后台时再双击桌面图标：再打开一次网页（服务没了就重新起）。
global serverPid

on run
	set serverPid to ""
	startPipeline()
end run

on reopen
	startPipeline()
end reopen

on idle
	if serverPid is not "" then
		try
			do shell script "/bin/ps -p " & serverPid & " -o command= | /usr/bin/grep -q app.server"
			return 15
		end try
	end if
	tell me to quit
	return 1
end idle

on startPipeline()
	try
		set pipeline to read (POSIX file ((POSIX path of (path to me)) & "Contents/Resources/pipeline_path")) as «class utf8»
	on error
		showMessage("桌面上的这个图标坏了，找不到流水线文件夹在哪。请到流水线文件夹里双击一次“启动流水线.command”，桌面图标会重新做一个。")
		return
	end try
	try
		set out to do shell script "/bin/bash " & quoted form of (pipeline & "/app/desktop/mac_launch.sh")
	on error msg number n
		if n is 127 then
			showMessage("找不到流水线文件夹：" & linefeed & pipeline & linefeed & linefeed & "流水线文件夹搬了地方或改了名字的话，到新的地方双击一次“启动流水线.command”，桌面图标会跟着更新。")
		else if n is 126 then
			showMessage("打不开流水线文件夹：" & linefeed & pipeline & linefeed & linefeed & "多半是系统问能不能访问“桌面”文件夹时点了“不允许”。请打开“系统设置 → 隐私与安全性 → 文件和文件夹”，给“预评价编制工作台”打开“桌面文件夹”，再双击桌面图标。")
		else
			showMessage(msg)
		end if
		return
	end try
	if out starts with "pid=" then set serverPid to text 5 thru -1 of out
end startPipeline

on showMessage(msg)
	activate
	display dialog msg with title "预评价编制工作台" buttons {"好"} default button 1 with icon caution
end showMessage
