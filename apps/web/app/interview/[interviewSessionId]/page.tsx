import { ProductionInterviewRoom } from "@/features/interview-room/components/ProductionInterviewRoom";

export default async function InterviewPage({
  params,
}: {
  params: Promise<{ interviewSessionId: string }>;
}) {
  const { interviewSessionId } = await params;
  return <ProductionInterviewRoom interviewSessionId={interviewSessionId} />;
}
