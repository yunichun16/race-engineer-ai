import { CornerStory } from "@/components/landing/CornerStory";
import { FanProjectNote } from "@/components/landing/FanProjectNote";
import { Hero } from "@/components/landing/Hero";
import { HowItWorks } from "@/components/landing/HowItWorks";
import { Limits } from "@/components/landing/Limits";
import { QuestionCards } from "@/components/landing/QuestionCards";

/**
 * The landing page (plan 7.1 as amended in plan 13, spec h): what it is and the Ask box, one
 * flagged corner explained step by step and then drawn by the real chart, the fan-project note,
 * three findings with their intervals, how it's built, and its limits.
 *
 * Prerendered and complete with the API stopped: the only call it makes is for the featured
 * corner (a saved snapshot first), and without it the story shows its illustration and no
 * numbers. It never shows an API status banner.
 */
export default function Home() {
  return (
    <>
      <Hero />
      <CornerStory />
      <FanProjectNote />
      <QuestionCards />
      <HowItWorks />
      <Limits />
    </>
  );
}
