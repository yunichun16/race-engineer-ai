import { SiteFooter } from "@/components/layout/SiteFooter";

// The content pages (/, /mistakes, /styles, /report): the page, then the footer.
export default function SiteLayout({ children }: LayoutProps<"/">) {
  return (
    <>
      <main id="main" tabIndex={-1} className="flex-1">
        {children}
      </main>
      <SiteFooter />
    </>
  );
}
