"use client";

import { ChevronRight, Receipt, RotateCcw, Store } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { FLOWS, type Action, type Kind, type Node, type Scenario } from "@/components/home/scenarios";

/** Sân khấu "trợ lý phán đoán" (cùng bố cục bản vẽ "Agent phán đoán"): sự kiện ⇒ trợ lý ⇒ cây ô theo cột (nguồn dữ liệu, khả năng giữ / loại) nối bằng
 *  đường cong tự vẽ ⇒ vạch "hệ thống kiểm soát" với dấu ở mỗi việc (mở / duyệt / hỏi) ⇒ thẻ kết quả. Thẻ chờ duyệt bấm Duyệt / Từ chối, thẻ hỏi bấm chọn.
 *  Chữ trong ô dùng foreignObject (tự xuống dòng). Khung rộng cố định, điện thoại cuộn ngang. Nhịp chuyển động theo bản vẽ (xem Edge / Play). Minh hoạ — không gọi core. */

const H = 660;
const CY = 340;
const NW = 170;
const NH = 62;
const CW = 336;
const CH = 134;
const CARDY = [110, 258, 406, 554];
// ô đầu vào (14..190) chứa cả nội dung thô ⇒ trợ lý ở 244, vùng phân tích từ 290
const AX = 244;
const ZONE = AX + 46;
const colX = (c: number) => ZONE + 44 + (c - 1) * 186;
const bez = (x1: number, y1: number, x2: number, y2: number) => {
  const m = (x1 + x2) / 2;
  return `M${x1} ${y1} C${m} ${y1}, ${m} ${y2}, ${x2} ${y2}`;
};

const TONE: Record<Kind | "done" | "no", { stroke: string; fill: string; text: string }> = {
  auto: { stroke: "stroke-ok", fill: "bg-ok/10 border-ok", text: "text-ok" },
  done: { stroke: "stroke-ok", fill: "bg-ok/10 border-ok", text: "text-ok" },
  wait: { stroke: "stroke-warn", fill: "bg-warn/10 border-warn", text: "text-warn" },
  ask: { stroke: "stroke-violet", fill: "bg-violet/10 border-violet", text: "text-violet" },
  no: { stroke: "stroke-muted-foreground/50", fill: "bg-surface border-hairline", text: "text-muted-foreground" },
};

export function AgentStage() {
  const [flowKey, setFlowKey] = useState(FLOWS[0].key);
  const flow = FLOWS.find((f) => f.key === flowKey) ?? FLOWS[0];
  const [scenKey, setScenKey] = useState(flow.scenarios[0].key);
  const scen = flow.scenarios.find((s) => s.key === scenKey) ?? flow.scenarios[0];
  const [run, setRun] = useState(0);

  return (
    <div className="grid gap-4">
      {/* thanh chọn: quy trình (nút phân đoạn) · đối tượng dùng; dưới là tình huống (thẻ gạch chân) và chuỗi khâu (dòng chữ nhỏ) — cùng kiểu với cả trang */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-full border border-hairline bg-surface p-1" role="group" aria-label="Chọn quy trình">
          {FLOWS.map((f) => {
            const on = f.key === flow.key;
            const Icon = f.key === "solo" ? Store : Receipt;
            return (
              <button
                key={f.key}
                type="button"
                aria-pressed={on}
                onClick={() => {
                  setFlowKey(f.key);
                  setScenKey(f.scenarios[0].key);
                  setRun((r) => r + 1);
                }}
                className={cn("inline-flex h-9 items-center gap-2 whitespace-nowrap rounded-full px-3 text-[13px] font-medium transition-colors sm:px-4 sm:text-[14px]", on ? "bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground")}
              >
                <Icon className="hidden size-4 sm:block" />
                {f.title}
              </button>
            );
          })}
        </div>
        <span className="bl-small text-muted-foreground">Dành cho {flow.who.toLowerCase()}</span>
      </div>

      <div className="grid gap-3 border-b border-hairline">
        <ol className="bl-small flex flex-wrap items-center gap-x-1.5 gap-y-1" aria-label={`Các khâu của quy trình ${flow.title}`}>
          {flow.chain.map((c, i) => (
            <li key={c} className="flex items-center gap-1.5">
              <span className={scen.chain.includes(i) ? "font-medium text-primary" : "text-muted-foreground"}>{c}</span>
              {i < flow.chain.length - 1 && <ChevronRight className="size-3 text-muted-foreground/60" aria-hidden />}
            </li>
          ))}
        </ol>
        <div className="-mb-px flex flex-wrap gap-x-6" role="group" aria-label="Chọn tình huống">
          {flow.scenarios.map((s) => {
            const on = s.key === scen.key;
            return (
              <button
                key={s.key}
                type="button"
                aria-pressed={on}
                onClick={() => {
                  setScenKey(s.key);
                  setRun((r) => r + 1);
                }}
                className={cn("t14m border-b-2 pt-1 pb-3 transition-colors", on ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}
              >
                <span className="tnum mr-1.5 font-normal text-muted-foreground">{s.time}</span>{s.tab}
              </button>
            );
          })}
        </div>
      </div>

      <Play key={`${scen.key}-${run}`} scen={scen} onReplay={() => setRun((r) => r + 1)} />
    </div>
  );
}

type Decision = { kind: "done" | "no"; label: string };
type Phase = "hidden" | "neutral" | Kind;   // thẻ: chưa hiện → hiện trung tính (nét vừa tới vạch) → mang màu loại việc

// nhịp lấy đúng bản vẽ "Agent phán đoán" (play / doAction): lời kể đi TRƯỚC, nét vẽ theo sau; nét có chấm dẫn đầu (ease-out); ô hiện khi nét tới;
// việc lệch 260ms, nét tới vạch 700ms rồi qua vạch 300ms; xong thì chấm nhỏ chạy dọc các nét đang dùng mỗi 450ms
const frame = () => new Promise<void>((r) => requestAnimationFrame(() => r()));

/** Một lượt chạy (một kịch bản tuần tự, chờ từng nét vẽ xong mới sang bước sau). Đổi tình huống / chạy lại ⇒ dựng lại (key) — lượt cũ tự dừng. */
function Play({ scen, onReplay }: { scen: Scenario; onReplay: () => void }) {
  const maxCol = Math.max(...scen.nodes.map((n) => n.col));
  const gate = colX(maxCol) + NW + 64;
  const cx = gate + 35;
  const vbw = cx + CW + 14;
  const byId = useMemo(() => Object.fromEntries(scen.nodes.map((n) => [n.id, n])), [scen]);

  const [say, setSay] = useState("");
  const [on, setOn] = useState<Set<string>>(new Set());
  const [phase, setPhase] = useState<Record<number, Phase>>({});
  const [dec, setDec] = useState<Record<number, Decision>>({});
  const [busy, setBusy] = useState(true);
  const [settled, setSettled] = useState(false);
  const [follow, setFollow] = useState(0);

  const scroller = useRef<HTMLDivElement>(null);
  const fx = useRef<SVGGElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const flowing = useRef(new Set<string>());   // nét đang dùng — chấm nhỏ chạy dọc khi xong
  const runRef = useRef<{ alive: boolean } | null>(null);   // lượt đang chạy — dựng lại / rời trang ⇒ alive = false, mọi bước còn treo tự dừng

  const path = (id: string) => svg.current?.querySelector<SVGPathElement>(`path[data-edge="${id}"]`) ?? null;

  /** Vẽ một nét trong ``dur`` ms: chấm dẫn đầu chạy theo đầu nét (ease-out cubic). Nét đứt chỉ hiện + chấm chạy. Chuyển động nhẹ (chấm nhỏ, nét mảnh) là
   *  phần chính của trang giới thiệu ⇒ chạy cả khi máy bật "giảm chuyển động" (anh Anh chọn 23/11/2026). */
  const draw = (id: string, dur: number, live: () => boolean) =>
    new Promise<void>((done) => {
      const p = path(id);
      if (!p || !live()) return done();
      const dashed = p.getAttribute("stroke-dasharray") !== "1";
      if (!fx.current) {
        p.style.strokeDashoffset = "0";
        p.style.opacity = "1";
        return done();
      }
      p.style.transition = "stroke .3s";
      p.style.opacity = "1";
      if (dashed) p.style.strokeDashoffset = "0";
      const L = p.getTotalLength();
      const dot = spawn(fx.current, 5.5, p);
      let t0: number | null = null;
      const f = (ts: number) => {
        if (!live()) {
          dot.remove();
          return done();
        }
        if (t0 === null) t0 = ts;
        const k = Math.min(1, (ts - t0) / dur);
        const e = 1 - Math.pow(1 - k, 3);
        const pt = p.getPointAtLength(L * e);
        dot.setAttribute("cx", String(pt.x));
        dot.setAttribute("cy", String(pt.y));
        if (!dashed) p.style.strokeDashoffset = String(1 - e);
        if (k < 1) requestAnimationFrame(f);
        else {
          dot.remove();
          done();
        }
      };
      requestAnimationFrame(f);
    });

  useEffect(() => {
    const run = { alive: true };
    runRef.current = run;
    const live = () => run.alive;
    const g = fx.current;
    const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

    const doAction = async (a: Action, i: number, delay: number) => {
      await sleep(delay);
      if (!live()) return;
      await draw(`a1-${i}`, 700, live);
      if (!live()) return;
      setPhase((m) => ({ ...m, [i]: "neutral" }));
      if (a.kind === "wait") {
        await sleep(250);
        if (live()) setPhase((m) => ({ ...m, [i]: "wait" }));
        return;
      }
      await draw(`a2-${i}`, 300, live);
      if (!live()) return;
      setPhase((m) => ({ ...m, [i]: a.kind }));
      if (a.kind === "auto") flowing.current.add(`a1-${i}`);
    };

    (async () => {
      await draw("ev", 300, live);
      for (let c = 1; c <= maxCol; c++) {
        if (!live()) return;
        setSay(scen.say[c - 1]);
        setFollow(colX(c));
        await sleep(c === 1 ? 500 : 1300);
        if (!live()) return;
        await Promise.all(
          scen.nodes
            .filter((n) => n.col === c)
            .map(async (n, j) => {
              await sleep(j * 140);
              if (!live()) return;
              await draw(`n-${n.id}`, 650, live);
              if (!live()) return;
              setOn((s) => new Set(s).add(n.id));
              if (!n.rej) flowing.current.add(`n-${n.id}`);
            }),
        );
        if (!live()) return;
        await sleep(600);
      }
      if (!live()) return;
      setSay(scen.say[maxCol]);
      setFollow(cx + CW);
      await sleep(1300);
      if (!live()) return;
      await Promise.all(scen.actions.map((a, i) => doAction(a, i, i * 260)));
      if (!live()) return;
      setBusy(false);
      setSettled(true);
    })();
    return () => {
      run.alive = false;
      g?.querySelectorAll("circle").forEach((c) => c.remove());
    };
    // một lượt cho mỗi lần dựng (đổi tình huống / chạy lại dựng lại qua key)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // xong ⇒ chấm nhỏ chạy dọc một nét đang dùng mỗi 450ms (dữ liệu đang chảy); thẻ trình duyệt ẩn ⇒ thôi
  useEffect(() => {
    if (!settled) return;
    const g = fx.current;
    let raf = 0;
    const id = setInterval(() => {
      const ids = [...flowing.current];
      if (document.hidden || !g || !ids.length) return;
      const p = path(ids[Math.floor(Math.random() * ids.length)]);
      if (!p) return;
      const dot = spawn(g, 3, p, 0.7);
      const L = p.getTotalLength();
      let t0: number | null = null;
      const f = (ts: number) => {
        if (t0 === null) t0 = ts;
        const k = Math.min(1, (ts - t0) / 1300);
        const pt = p.getPointAtLength(L * k);
        dot.setAttribute("cx", String(pt.x));
        dot.setAttribute("cy", String(pt.y));
        if (k < 1) raf = requestAnimationFrame(f);
        else dot.remove();
      };
      raf = requestAnimationFrame(f);
    }, 450);
    return () => {
      clearInterval(id);
      cancelAnimationFrame(raf);
    };
  }, [settled]);

  // khung hẹp (điện thoại) cuộn ngang theo phần đang lộ — như người kể chỉ tay
  useEffect(() => {
    const el = scroller.current;
    if (!el || !follow || el.scrollWidth <= el.clientWidth + 2) return;
    const k = el.scrollWidth / vbw;
    el.scrollTo({ left: Math.max(0, follow * k - el.clientWidth * 0.6), behavior: "smooth" });
  }, [follow, vbw]);

  /** Người bấm Duyệt / chọn: nét qua vạch vẽ lại (xanh) rồi thẻ thành "đã thực hiện". Từ chối: nét tới vạch thành nét đứt xám. */
  const decide = async (i: number, d: Decision) => {
    setDec((m) => ({ ...m, [i]: d }));
    if (d.kind === "no") return;
    const p = path(`a2-${i}`);
    if (p) {
      p.style.transition = "none";
      p.style.opacity = "0";
      p.style.strokeDashoffset = "1";
    }
    await frame();
    await frame();   // chờ React đổi màu / bỏ nét đứt của nét trước khi vẽ
    const run = runRef.current;
    await draw(`a2-${i}`, 350, () => !!run?.alive);
    flowing.current.add(`a1-${i}`);
  };

  const shownCount = (rej: boolean) => scen.nodes.filter((n) => on.has(n.id) && !!n.rej === rej).length;
  const kindOf = (i: number): Phase | "done" | "no" => dec[i]?.kind ?? phase[i] ?? "hidden";
  const count = (k: string) => scen.actions.filter((_, i) => kindOf(i) === k).length;
  const flagOn = scen.nodes.some((n) => n.col === 1 && on.has(n.id));

  return (
    <div className="grid gap-3">
      {/* lời kể của trợ lý: một dòng trên nền thẻ như các khối khác của trang; chấm xanh nhịp khi đang chạy */}
      <div className="flex min-h-[52px] flex-wrap items-center gap-3 rounded-2xl border border-hairline bg-surface px-4 py-3">
        <span className="bl-small inline-flex items-center gap-2 font-medium text-primary">
          <span className={cn("size-2 rounded-full bg-primary", busy && "animate-pulse")} aria-hidden /> {busy ? "Trợ lý đang xử lý" : "Trợ lý đã xong"}
        </span>
        <p className="bl-body min-w-0 flex-[1_1_320px]" aria-live="polite">{say}</p>
        <button type="button" onClick={onReplay} className="t14m inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-foreground hover:bg-surface2">
          <RotateCcw className="size-3.5" /> Chạy lại
        </button>
      </div>

      <div
        ref={scroller}
        className="overflow-x-auto rounded-2xl border border-hairline bg-surface"
        style={{ backgroundImage: "radial-gradient(var(--hairline) 1px, transparent 1px)", backgroundSize: "22px 22px" }}
      >
        <svg ref={svg} viewBox={`0 0 ${vbw} ${H}`} className="block h-auto w-full" style={{ minWidth: 980 }} role="img" aria-label={`Sơ đồ trợ lý xử lý: ${scen.tab}`}>
          <rect x={ZONE} y={40} width={gate - 40 - ZONE} height={580} rx={14} className="fill-primary/5" />
          <rect x={gate - 2} y={36} width={4} height={588} rx={2} className="fill-foreground/30" />
          <Head x={102} n={1} label="Đầu vào" cls="fill-[#F0642D]" />
          <Head x={(ZONE + gate - 40) / 2} n={2} label="Trợ lý đọc dữ liệu, thử từng khả năng" cls="fill-primary" />
          <Head x={gate} n={3} label="Hệ thống kiểm soát" cls="fill-foreground" />
          <Head x={cx + CW / 2} n={4} label="Kết quả" cls="fill-ok" />
          <text x={gate} y={646} textAnchor="middle" className="fill-muted-foreground text-[11px]">do doanh nghiệp đặt: phân quyền · hạn mức · phê duyệt bắt buộc — trợ lý không đổi được</text>

          {/* đường nối: sự kiện → trợ lý → ô cột 1 → ô con; nét tới vạch / qua vạch của từng việc */}
          <Path edge="ev" d={`M190 ${CY} H${AX - 47}`} cls="stroke-[#F0642D]" />
          {scen.nodes.map((n) => {
            const p = n.parent ? byId[n.parent] : null;
            const d = p ? bez(colX(p.col) + NW, p.y, colX(n.col), n.y) : bez(AX + 40, CY, colX(n.col), n.y);
            return <Path key={`e-${n.id}`} edge={`n-${n.id}`} d={d} dashed={n.rej} cls={n.rej ? "stroke-muted-foreground/50" : "stroke-primary"} />;
          })}
          {scen.actions.map((a, i) => {
            const ask = a.kind === "ask";
            const stopX = gate - 30;
            const k = kindOf(i);
            const tone = TONE[k === "done" || k === "no" ? k : a.kind].stroke;
            return (
              <g key={`a-${i}`}>
                <Path edge={`a1-${i}`} d={bez(colX(byId[a.from].col) + NW, byId[a.from].y, ask ? stopX - 10 : gate, CARDY[i])} dashed={k === "no"} cls={tone} />
                <Path edge={`a2-${i}`} d={ask ? `M${stopX + 10} ${CARDY[i]} H${cx}` : `M${gate} ${CARDY[i]} H${cx}`} dashed={ask && k !== "done"} cls={tone} />
              </g>
            );
          })}

          {/* đầu vào (màu thương hiệu: nơi mọi thứ bắt đầu) — kèm nguyên văn dữ liệu thô */}
          <foreignObject x={14} y={CY - 84} width={176} height={168}>
            <div className="flex h-full flex-col gap-1.5 rounded-2xl p-3 text-white" style={{ background: "linear-gradient(160deg, #FFA45C, #F0642D)" }}>
              <span className="text-[11px] font-semibold text-white/85">{scen.time} · {scen.rawK}</span>
              <span className="text-[13.5px] leading-tight font-semibold">{scen.event}</span>
              <code className="mt-auto line-clamp-4 rounded-lg bg-white/20 px-2 py-1.5 font-mono text-[11px] leading-snug [overflow-wrap:anywhere]">{scen.raw}</code>
            </div>
          </foreignObject>
          <circle cx={AX} cy={CY} r={40} className="fill-primary/10 stroke-primary" strokeWidth={1.5} />
          <circle cx={AX} cy={CY} r={47} fill="none" className={cn("stroke-primary/60", busy && "animate-spin")} strokeWidth={2} strokeDasharray={busy ? "10 8" : undefined} style={{ transformOrigin: `${AX}px ${CY}px`, animationDuration: "2.4s" }} />
          <text x={AX} y={CY - 4} textAnchor="middle" className="fill-foreground text-[13.5px] font-semibold">Trợ lý</text>
          <text x={AX} y={CY + 13} textAnchor="middle" className="fill-muted-foreground text-[11px]">{scen.role}</text>

          {/* ô theo cột: hiện khi nét tới */}
          {scen.nodes.map((n) => (
            <NodeBox key={n.id} n={n} on={on.has(n.id)} />
          ))}
          <foreignObject x={colX(1)} y={CY + NH / 2 + 6} width={NW - 6} height={64} style={{ opacity: flagOn ? 1 : 0, transition: "opacity .35s" }}>
            <p className="text-[11.5px] leading-snug font-semibold text-bad">⚑ {scen.flag}</p>
          </foreignObject>

          {/* dấu trên vạch + thẻ kết quả */}
          {scen.actions.map((a, i) => (
            <Mark key={`m-${i}`} a={a} y={CARDY[i]} gate={gate} shown={kindOf(i) !== "hidden"} k={kindOf(i)} />
          ))}
          {scen.actions.map((a, i) => (
            <Card key={`c-${i}`} a={a} y={CARDY[i]} x={cx} phase={phase[i] ?? "hidden"} decision={dec[i]} onDecide={(d) => void decide(i, d)} />
          ))}
          <g ref={fx} aria-hidden />
        </svg>
      </div>

      <ul className="flex flex-wrap gap-2" aria-label="Chú giải">
        <Key n={shownCount(false)} label="dữ liệu, khả năng phù hợp" line="bg-primary" />
        <Key n={shownCount(true)} label="khả năng bị loại" line="border-t-2 border-dashed border-muted-foreground/60" />
        <Key n={count("auto") + count("done")} label="đã làm (trong hạn mức)" line="bg-ok" />
        <Key n={count("wait")} label="chờ người duyệt" line="bg-warn" />
        <Key n={count("ask")} label="trợ lý hỏi ý kiến" line="bg-violet" />
      </ul>
    </div>
  );
}

function spawn(g: SVGGElement, r: number, p: SVGPathElement, opacity = 1): SVGCircleElement {
  const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  dot.setAttribute("r", String(r));
  dot.setAttribute("fill", getComputedStyle(p).stroke);
  dot.setAttribute("opacity", String(opacity));
  g.appendChild(dot);
  return dot;
}

/** Một nét nối, ẩn sẵn (pathLength = 1 ⇒ vạch đứt dài 1, lệch 1 = chưa vẽ). Kịch bản của Play vẽ nó bằng tay (``draw``) — React không đụng style sau khi dựng. */
function Path({ edge, d, cls, dashed = false }: { edge: string; d: string; cls: string; dashed?: boolean }) {
  return (
    <path
      data-edge={edge}
      d={d}
      pathLength={1}
      fill="none"
      strokeWidth={2}
      strokeLinecap="round"
      className={cn(cls, "[transition:stroke_.3s]")}
      strokeDasharray={dashed ? "0.012 0.014" : 1}
      style={{ opacity: 0, strokeDashoffset: 1 }}
    />
  );
}

/** Dấu trên vạch: khoá mở (làm ngay), khoá "duyệt" (chờ người), hoặc dấu hỏi TRƯỚC vạch (trợ lý tự dừng hỏi). Chờ thì thở nhẹ. */
function Mark({ a, y, gate, shown, k }: { a: Action; y: number; gate: number; shown: boolean; k: Phase | "done" | "no" }) {
  const ask = a.kind === "ask";
  const stopX = gate - 30;
  const tone = TONE[k === "done" || k === "no" ? k : a.kind].stroke;
  const waiting = k === "wait" || k === "ask";
  return (
    <g style={{ opacity: shown ? 1 : 0, transition: "opacity .3s" }}>
      {ask ? (
        <>
          <circle cx={stopX} cy={y} r={10} className={cn("fill-surface", tone, waiting && "animate-pulse")} strokeWidth={2} />
          <text x={stopX} y={y + 4} textAnchor="middle" className="fill-violet text-[12px] font-bold">?</text>
          <text x={stopX} y={y + 24} textAnchor="middle" className="fill-violet text-[9.5px] font-semibold">hỏi ý kiến</text>
        </>
      ) : (
        <>
          <circle cx={gate} cy={y} r={9} className={cn("fill-surface [transition:stroke_.3s]", tone, waiting && "animate-pulse")} strokeWidth={2.5} />
          {a.kind === "wait" && (
            <text x={gate} y={y - 15} textAnchor="middle" className={cn("text-[9.5px] font-semibold", k === "no" ? "fill-muted-foreground" : "fill-warn")}>
              duyệt
            </text>
          )}
        </>
      )}
    </g>
  );
}

function NodeBox({ n, on }: { n: Node; on: boolean }) {
  const x = colX(n.col);
  return (
    <foreignObject x={x} y={n.y - NH / 2} width={NW} height={NH} style={{ opacity: on ? 1 : 0, transition: "opacity .35s" }}>
      <div
        className={cn(
          "flex h-full flex-col justify-center rounded-[10px] border-[1.5px] px-2.5 leading-tight",
          n.rej ? "border-dashed border-muted-foreground/50 bg-surface2/60 text-muted-foreground" : "border-primary",
          n.hyp && !n.rej ? "bg-primary/10" : !n.rej && "bg-surface",
        )}
      >
        {n.hyp && <span className={cn("text-[9.5px] font-semibold", n.rej ? "text-muted-foreground" : "text-primary")}>{n.rej ? "Loại bỏ" : "Phù hợp"}</span>}
        <span className={cn("line-clamp-1 text-[12.5px] font-semibold", n.rej && "line-through")}>{n.t}</span>
        <span className={cn("line-clamp-2 text-[11.5px]", n.rej ? "text-muted-foreground" : "font-medium text-primary")}>{n.res}</span>
      </div>
    </foreignObject>
  );
}

function Card({ a, x, y, phase, decision, onDecide }: { a: Action; x: number; y: number; phase: Phase; decision?: Decision; onDecide: (d: Decision) => void }) {
  const k = decision?.kind ?? phase;
  const tone = k === "hidden" || k === "neutral" ? { fill: "bg-surface border-hairline", text: "text-muted-foreground" } : TONE[k];
  const head = decision?.label ?? (phase === "auto" ? "Đã thực hiện" : phase === "wait" ? `Chờ ${a.who ?? "người"} duyệt` : phase === "ask" ? "Trợ lý xin ý kiến" : " ");
  const open = !decision && phase === a.kind;   // nút chỉ hiện khi thẻ đã mang màu loại việc
  return (
    <foreignObject x={x} y={y - CH / 2} width={CW} height={CH} style={{ opacity: phase === "hidden" ? 0 : 1, transition: "opacity .35s", pointerEvents: phase === "hidden" ? "none" : "auto" }}>
      <div className={cn("flex h-full flex-col gap-1 overflow-hidden rounded-xl border-[1.5px] px-3.5 py-2.5 text-[12px] leading-snug transition-colors duration-300", tone.fill)}>
        <div className="flex min-h-[22px] items-center justify-between gap-2">
          <span className={cn("text-[11px] font-semibold transition-colors duration-300", tone.text)}>{head}</span>
          {a.kind === "wait" && open && (
            <span className="flex gap-1.5 animate-in fade-in duration-300">
              <button type="button" onClick={() => onDecide({ kind: "no", label: `${cap(a.who ?? "người duyệt")} từ chối · không thực hiện` })} className="rounded-full border border-hairline bg-surface px-2.5 py-0.5 text-[11.5px] font-semibold text-muted-foreground hover:text-foreground">
                Từ chối
              </button>
              <button type="button" onClick={() => onDecide({ kind: "done", label: `Đã thực hiện · ${a.who ?? "người duyệt"} đã duyệt` })} className="rounded-full bg-warn px-2.5 py-0.5 text-[11.5px] font-semibold text-white hover:opacity-90">
                Duyệt
              </button>
            </span>
          )}
        </div>
        <p className="line-clamp-2 text-[13.5px] font-semibold">{a.title}</p>
        <p className="line-clamp-2">
          <span className="font-semibold text-primary">Trợ lý đề xuất · </span>
          <span className="text-muted-foreground">{a.why}</span>
        </p>
        {a.rule && (
          <p className="line-clamp-2">
            <span className={cn("font-semibold", a.kind === "auto" ? "text-ok" : "text-warn")}>{a.kind === "auto" ? "Kiểm soát · " : "Cần duyệt · "}</span>
            <span className="text-muted-foreground">{a.rule}</span>
          </p>
        )}
        {a.kind === "ask" && open && (
          <div className="mt-auto flex flex-wrap gap-1.5 animate-in fade-in duration-300">
            {(a.opts ?? []).map((o) => (
              <button key={o} type="button" onClick={() => onDecide({ kind: "done", label: `Đã thực hiện · bạn chọn: ${o}` })} className="rounded-full bg-violet px-3 py-1 text-[11.5px] font-semibold text-white hover:opacity-90">
                {o}
              </button>
            ))}
          </div>
        )}
      </div>
    </foreignObject>
  );
}

const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

function Key({ n, label, line }: { n: number; label: string; line: string }) {
  return (
    <li className="bl-small inline-flex items-center gap-2 rounded-full bg-surface px-3 py-1.5 text-muted-foreground">
      <span className={cn("h-0.5 w-5 rounded-full", line)} aria-hidden />
      <b className="t14m tnum text-foreground">{n}</b> {label}
    </li>
  );
}

/** Tiêu đề cột của sơ đồ, đánh số theo thứ tự đọc 1 → 4. */
function Head({ x, n, label, cls }: { x: number; n: number; label: string; cls: string }) {
  return (
    <text x={x} y={26} textAnchor="middle" className={cn("text-[12px] font-semibold", cls)}>
      <tspan className="font-bold">{n}</tspan>
      {"  "}
      {label}
    </text>
  );
}
