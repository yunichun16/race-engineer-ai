// The report's figures, served at /report/figures/<file>: the PNGs in ../report/figures, read and
// prerendered at build time. Nothing here reads the request; any other file name is a 404.
import { listFigures, readFigure } from "@/lib/report/load";

export const dynamic = "force-static";
export const dynamicParams = false;

export async function generateStaticParams() {
  return (await listFigures()).map((file) => ({ file }));
}

export async function GET(_request: Request, { params }: RouteContext<"/report/figures/[file]">) {
  const { file } = await params;
  const bytes = await readFigure(file);
  return new Response(new Uint8Array(bytes), {
    headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=3600" },
  });
}
