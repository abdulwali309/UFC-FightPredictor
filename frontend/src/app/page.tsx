import Link from "next/link";

export default function HomePage() {
  return (
    <section className="stack home-minimal">
      <div className="panel home-intro">
        <h2>UFCML Features</h2>
        <p className="helper-text">Choose a page below to run predictions, review upcoming cards, or inspect past results.</p>
      </div>

      <div className="feature-grid home-minimal-grid">
        <Link href="/predict" className="feature-card home-card-primary">
          <h3>Custom Matchup</h3>
          <p className="helper-text">Compare any two fighters and generate a win-probability prediction.</p>
        </Link>
        <Link href="/upcoming" className="feature-card">
          <h3>Upcoming Card</h3>
          <p className="helper-text">View predicted outcomes for scheduled UFC events and fight cards.</p>
        </Link>
        <Link href="/results" className="feature-card">
          <h3>Fight Results</h3>
          <p className="helper-text">Review completed fights, previous picks, and prediction accuracy.</p>
        </Link>
      </div>
    </section>
  );
}
