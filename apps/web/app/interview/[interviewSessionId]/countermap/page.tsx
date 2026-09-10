import { ProductionCounterMapPage } from "@/features/countermap/ProductionCounterMapPage";

export default async function CounterMapPage({
  params,
}: {
  params: Promise<{ interviewSessionId: string }>;
}) {
  const { interviewSessionId } = await params;
  return <ProductionCounterMapPage interviewSessionId={interviewSessionId} />;
}
