import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";

/** Model output is untrusted — sanitized Markdown only, no raw HTML execution. */
export function SafeMarkdown({ content }: { content: string }) {
  if (!content.trim()) {
    return <p className="muted">No content.</p>;
  }
  return (
    <div className="safe-markdown">
      <ReactMarkdown rehypePlugins={[rehypeSanitize]}>{content}</ReactMarkdown>
    </div>
  );
}
