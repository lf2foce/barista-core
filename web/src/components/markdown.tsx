import { parseInline, parseMarkdown } from "@/lib/markdown";
import { cn } from "@/lib/utils";

function Inline({ text }: { text: string }) {
  return (
    <>
      {parseInline(text).map((p, i) => {
        if (p.kind === "bold") return <strong key={i} className="font-semibold">{p.text}</strong>;
        if (p.kind === "code") return <code key={i} className="rounded bg-muted px-1 font-mono text-[0.92em]">{p.text}</code>;
        if (p.kind === "link") return <a key={i} href={p.href} target="_blank" rel="noopener noreferrer" className="text-primary underline-offset-2 hover:underline">{p.text}</a>;
        return <span key={i}>{p.text}</span>;
      })}
    </>
  );
}

export function Markdown({ text, className }: { text: string; className?: string }) {
  const blocks = parseMarkdown(text);
  return (
    <div className={cn("t14 flex flex-col gap-1.5 break-words", className)}>
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "heading":
            return <p key={i} className={cn("pt-1", b.level <= 2 ? "t16" : "t14m")}><Inline text={b.text} /></p>;
          case "bullet":
            return (
              <div key={i} className="flex gap-2" style={{ paddingLeft: b.level * 16 }}>
                <span className="w-4 shrink-0 text-muted-foreground">{b.marker}</span>
                <span><Inline text={b.text} /></span>
              </div>
            );
          case "table":
            return (
              <div key={i} className="overflow-x-auto rounded-lg border border-border">
                <table className="t12 w-full border-collapse">
                  <tbody>
                    {b.rows.map((r, ri) => (
                      <tr key={ri} className={ri === 0 ? "bg-muted font-medium" : "border-t border-border"}>
                        {r.map((c, ci) => (
                          <td key={ci} className="px-2 py-1.5 align-top"><Inline text={c} /></td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "rule":
            return <hr key={i} className="my-1 border-border" />;
          default:
            return <p key={i}><Inline text={b.text} /></p>;
        }
      })}
    </div>
  );
}
