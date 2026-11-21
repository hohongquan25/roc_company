// Bảng định tuyến dùng chung: trang đầy đủ (chế độ bảng) và cửa sổ nổi trong văn phòng 2D.
import Overview from "./pages/Overview";
import Jobs from "./pages/Jobs";
import Requests, { RequestDetail } from "./pages/Requests";
import Memory from "./pages/Memory";
import Reports from "./pages/Reports";
import Settings from "./pages/Settings";
import Approvals from "./pages/Approvals";
import { EmployeeView } from "./pages/EmployeeView";
import Staff from "./pages/Staff";

export const TITLES: Record<string, string> = {
  "/": "Tổng quan", "/overview": "Tổng quan", "/requests": "Yêu cầu nhạc", "/request": "Yêu cầu", "/staff": "Nhân viên",
  "/jobs": "Bảng công việc", "/memory": "Bộ nhớ", "/reports": "Báo cáo & nhật ký", "/settings": "Cài đặt kết nối",
  "/approvals": "Cần bạn xử lý", "/employee": "Nhân viên",
};

export function titleOf(path: string): string {
  return TITLES[path] || TITLES["/" + (path.split("/")[1] || "")] || "ROC";
}

export function renderPage(path: string) {
  const m = (re: RegExp) => path.match(re);
  let r;
  if (path === "/" || path === "/overview") return <Overview />;
  if (path === "/requests") return <Requests />;
  if ((r = m(/^\/request\/(\d+)$/))) return <RequestDetail id={Number(r[1])} />;
  if (path === "/staff") return <Staff />;
  if (path === "/jobs") return <Jobs />;
  if (path === "/memory") return <Memory />;
  if (path === "/reports") return <Reports />;
  if (path === "/settings") return <Settings />;
  if (path === "/approvals") return <Approvals />;
  if ((r = m(/^\/employee\/([\w-]+)$/))) return <EmployeeView id={r[1]} />;
  return null;
}
