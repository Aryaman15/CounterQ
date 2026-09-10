import { ProductionSessionReportPage } from "@/features/interview-room/components/ProductionSessionReportPage";

export default async function SessionReportPage({
  params,
}: {
  params: Promise<{ interviewSessionId: string }>;
}) {
  const { interviewSessionId } = await params;
  return <ProductionSessionReportPage interviewSessionId={interviewSessionId} />;
}
