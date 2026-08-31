import { InterviewRoom } from "@/features/interview-room/components/InterviewRoom";
import { demoInterviewFixture } from "@/features/interview-room/fixtures/demoInterview";

export default function InterviewDemoPage() {
  return <InterviewRoom fixture={demoInterviewFixture} allowFixturePreview={false} />;
}
