import ReactMarkdown from "react-markdown";

export function MarkdownPanel({
  title,
  markdown,
}: {
  title: string;
  markdown?: string | null;
}) {
  return (
    <section className="surface-card markdown-card">
      <div className="section-heading">
        <h2>{title}</h2>
      </div>
      {markdown ? (
        <div className="markdown-body">
          <ReactMarkdown>{markdown}</ReactMarkdown>
        </div>
      ) : (
        <p className="muted-copy">No markdown is available yet.</p>
      )}
    </section>
  );
}
