import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

export function middleware(request: NextRequest) {
  // Lấy giá trị cookie access_token do Backend thiết lập
  const token = request.cookies.get('access_token')?.value

  // Bảo vệ các router dành cho Admin
  if (request.nextUrl.pathname.startsWith('/admin')) {
    if (!token) {
      // Bắt buộc chuyển hướng về trang đăng nhập với role admin
      return NextResponse.redirect(new URL('/login?as=admin', request.url))
    }
  }

  return NextResponse.next()
}

// Chỉ áp dụng Middleware cho các đường dẫn cụ thể để tối ưu hiệu năng
export const config = {
  matcher: [
    '/admin/:path*',
  ],
}
