/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',  // Static export for Cloudflare Pages
  images: {
    unoptimized: true,  // Required for static export
  },
  // The dashboard calls the Worker API, which may be on a different origin.
  // Since we use cookie auth (SameSite=Strict), we deploy the dashboard
  // on a subpath of the Worker domain, or configure CORS.
  trailingSlash: true,
};

export default nextConfig;
