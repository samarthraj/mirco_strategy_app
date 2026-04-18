"""
LLM Semantic Analyzer for MicroStrategy SQL Reports
====================================================
Uses OpenAI's GPT and Embedding APIs to:
1. Generate business narrative + category for each report
2. Create semantic embeddings and cluster similar reports
3. Evaluate duplicate groups and identify true duplicates

Standalone script - requires OPENAI_API_KEY environment variable.
"""

import os
import re
import json
import argparse
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import numpy as np

from openai import OpenAI
from pydantic import BaseModel


# =============================================================================
# Configuration
# =============================================================================

# Pre-defined business categories for report classification
REPORT_CATEGORIES = [
    "Pricing & MSRP",
    "Projections & Forecasting",
    "Product Catalog",
    "Regional Analysis",
    "Inventory & Supply",
    "Financial & Costing",
    "Seasonal Planning",
    "Ad-hoc / Custom"
]

# Models (exact as per user specification)
EMBEDDING_MODEL = "text-embedding-3-large"  # 3072 dimensions
TEXT_MODEL = "gpt-5.4"


# =============================================================================
# Pydantic Models for Structured Outputs
# =============================================================================

class ReportAnnotation(BaseModel):
    """Step 1: LLM-generated narrative and category for a report."""
    narrative: str
    category: str
    key_entities: list[str]
    business_purpose: str


class ReportDuplicateVerdict(BaseModel):
    """Verdict for a single report in a duplicate group."""
    report_id: str
    report_name: str
    is_duplicate: bool
    duplicate_of: list[str]
    rationale: str


class GroupEvaluation(BaseModel):
    """Step 3: LLM evaluation of a duplicate group."""
    group_id: str
    overall_assessment: str
    canonical_report: str
    canonical_rationale: str
    verdicts: list[ReportDuplicateVerdict]
    consolidation_recommendation: str


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class SQLReportInfo:
    """Information about a SQL report for analysis."""
    report_id: str
    report_name: str
    project: str
    source_type: str
    file_path: str
    sql_preview: str  # First N lines of SQL
    tables: List[str]
    table_count: int
    
    
@dataclass
class SemanticAnnotation:
    """Result of Step 1 - LLM annotation."""
    report_id: str
    report_name: str
    narrative: str
    category: str
    key_entities: List[str]
    business_purpose: str
    embedding: List[float]
    

@dataclass
class SemanticGroup:
    """Result of Step 2 - Semantic clustering."""
    group_id: str
    category: str
    avg_similarity: float
    member_ids: List[str]
    member_names: List[str]


# =============================================================================
# Helper Functions
# =============================================================================

def parse_sql_header(content: str) -> Dict[str, str]:
    """Extract metadata from SQL file header."""
    lines = content.split('\n')[:10]
    header_text = '\n'.join(lines)
    
    source_match = re.search(r'--\s*Source\s*:\s*(.+)', header_text)
    name_match = re.search(r'--\s*Name\s*:\s*(.+)', header_text)
    id_match = re.search(r'--\s*ID\s*:\s*([A-F0-9]+)', header_text)
    project_match = re.search(r'--\s*Project\s*:\s*(.+)', header_text)
    
    return {
        'source': source_match.group(1).strip() if source_match else 'UNKNOWN',
        'name': name_match.group(1).strip() if name_match else 'Unknown Report',
        'id': id_match.group(1).strip() if id_match else '',
        'project': project_match.group(1).strip() if project_match else 'Unknown'
    }


def extract_tables_from_sql(sql_content: str) -> List[str]:
    """Extract table names from SQL content."""
    # Remove comments
    sql_clean = re.sub(r'--.*$', '', sql_content, flags=re.MULTILINE)
    sql_clean = re.sub(r'/\*.*?\*/', '', sql_clean, flags=re.DOTALL)
    
    # Find FROM and JOIN table references
    table_pattern = r'(?:from|join)\s+"?([a-zA-Z_][a-zA-Z0-9_]*)"?\s*\.\s*"?([a-zA-Z_][a-zA-Z0-9_]*)"?'
    matches = re.findall(table_pattern, sql_clean, re.IGNORECASE)
    
    tables = []
    for schema, table in matches:
        full_name = f"{schema}.{table}"
        if full_name not in tables:
            tables.append(full_name)
    
    return tables


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    a = np.array(vec1)
    b = np.array(vec2)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


# =============================================================================
# LLM Semantic Analyzer
# =============================================================================

class LLMSemanticAnalyzer:
    """
    Main analyzer class implementing the 3-step LLM pipeline:
    1. Generate narrative + category per report
    2. Semantic clustering using embeddings
    3. LLM evaluation of duplicate groups
    """
    
    def __init__(self, sql_dir: str, similarity_threshold: float = 0.80):
        self.sql_dir = Path(sql_dir)
        self.similarity_threshold = similarity_threshold
        self.client = OpenAI()
        
        # Data storage
        self.reports: List[SQLReportInfo] = []
        self.annotations: Dict[str, SemanticAnnotation] = {}
        self.semantic_groups: List[SemanticGroup] = []
        self.group_evaluations: List[GroupEvaluation] = []
        
        # Stats
        self.stats = {
            'total_reports': 0,
            'annotated_reports': 0,
            'semantic_groups': 0,
            'evaluated_groups': 0,
            'true_duplicates_found': 0,
            'api_calls': {'embedding': 0, 'text_gen': 0}
        }
    
    def load_reports(self) -> int:
        """Load all SQL reports from the directory."""
        print(f"\n📁 Loading SQL files from: {self.sql_dir}")
        
        sql_files = list(self.sql_dir.glob("*.sql"))
        
        for sql_file in sql_files:
            try:
                content = sql_file.read_text(encoding='utf-8', errors='ignore')
                header = parse_sql_header(content)
                
                # Get SQL preview (first 100 lines after header)
                lines = content.split('\n')
                sql_body_start = 0
                for i, line in enumerate(lines):
                    if line.strip().startswith('select') or line.strip().startswith('SELECT'):
                        sql_body_start = i
                        break
                sql_preview = '\n'.join(lines[sql_body_start:sql_body_start+100])
                
                # Extract tables
                tables = extract_tables_from_sql(content)
                
                report = SQLReportInfo(
                    report_id=header['id'] or sql_file.stem,
                    report_name=header['name'],
                    project=header['project'],
                    source_type=header['source'],
                    file_path=str(sql_file),
                    sql_preview=sql_preview[:3000],  # Limit preview size
                    tables=tables,
                    table_count=len(tables)
                )
                self.reports.append(report)
                
            except Exception as e:
                print(f"   ⚠️ Error loading {sql_file.name}: {e}")
        
        self.stats['total_reports'] = len(self.reports)
        print(f"   ✅ Loaded {len(self.reports)} reports")
        return len(self.reports)
    
    def step1_annotate_reports(self, batch_size: int = 10) -> int:
        """
        Step 1: Generate narrative + category for each report using LLM.
        Uses structured output with Pydantic model.
        """
        print(f"\n🤖 Step 1: Generating narratives and categories...")
        print(f"   Model: {TEXT_MODEL}")
        print(f"   Categories: {', '.join(REPORT_CATEGORIES)}")
        
        categories_list = '\n'.join([f"  - {cat}" for cat in REPORT_CATEGORIES])
        
        for i, report in enumerate(self.reports):
            print(f"   [{i+1}/{len(self.reports)}] Annotating: {report.report_name[:50]}...", end=" ")
            
            try:
                # Build prompt
                prompt = f"""Analyze this MicroStrategy SQL report and provide:
1. A concise business narrative (2-3 sentences) explaining what the report does
2. The most appropriate category from the list below
3. Key business entities involved (e.g., products, regions, seasons)
4. The primary business purpose

Report Name: {report.report_name}
Project: {report.project}
Tables Used ({report.table_count}): {', '.join(report.tables[:20])}

SQL Preview:
```sql
{report.sql_preview[:2000]}
```

Available Categories:
{categories_list}

Provide your analysis."""

                # Call LLM with structured output
                response = self.client.responses.parse(
                    model=TEXT_MODEL,
                    input=[
                        {
                            "role": "system",
                            "content": "You are an expert business analyst specializing in retail and supply chain reporting. Analyze SQL reports and classify them by business purpose."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    text_format=ReportAnnotation,
                )
                
                self.stats['api_calls']['text_gen'] += 1
                
                result = response.output_parsed
                
                # Generate embedding for the narrative
                embed_response = self.client.embeddings.create(
                    input=f"{result.narrative} {result.business_purpose} {' '.join(result.key_entities)}",
                    model=EMBEDDING_MODEL
                )
                embedding = embed_response.data[0].embedding
                self.stats['api_calls']['embedding'] += 1
                
                # Store annotation
                annotation = SemanticAnnotation(
                    report_id=report.report_id,
                    report_name=report.report_name,
                    narrative=result.narrative,
                    category=result.category,
                    key_entities=result.key_entities,
                    business_purpose=result.business_purpose,
                    embedding=embedding
                )
                self.annotations[report.report_id] = annotation
                self.stats['annotated_reports'] += 1
                
                print(f"✓ [{result.category}]")
                
            except Exception as e:
                print(f"✗ Error: {e}")
                continue
        
        print(f"\n   ✅ Annotated {self.stats['annotated_reports']} reports")
        return self.stats['annotated_reports']
    
    def step2_cluster_semantically(self) -> int:
        """
        Step 2: Cluster reports by semantic similarity using embeddings.
        Groups reports with cosine similarity >= threshold.
        """
        print(f"\n🔗 Step 2: Semantic clustering (threshold: {self.similarity_threshold})")
        
        # Get list of annotated reports
        annotated_ids = list(self.annotations.keys())
        n = len(annotated_ids)
        
        if n < 2:
            print("   ⚠️ Not enough reports to cluster")
            return 0
        
        # Calculate similarity matrix
        print(f"   Calculating {n}x{n} similarity matrix...")
        similarities = {}
        for i in range(n):
            for j in range(i + 1, n):
                id1, id2 = annotated_ids[i], annotated_ids[j]
                sim = cosine_similarity(
                    self.annotations[id1].embedding,
                    self.annotations[id2].embedding
                )
                if sim >= self.similarity_threshold:
                    similarities[(id1, id2)] = sim
        
        print(f"   Found {len(similarities)} pairs above threshold")
        
        # Build clusters using Union-Find approach
        # Group by same category first, then by similarity
        category_groups: Dict[str, List[str]] = {}
        for report_id, annotation in self.annotations.items():
            cat = annotation.category
            if cat not in category_groups:
                category_groups[cat] = []
            category_groups[cat].append(report_id)
        
        # Within each category, cluster by similarity
        group_counter = 0
        clustered = set()
        
        for category, report_ids in category_groups.items():
            if len(report_ids) < 2:
                continue
            
            # Find connected components within this category
            for i, id1 in enumerate(report_ids):
                if id1 in clustered:
                    continue
                    
                # Start a new cluster
                cluster_members = [id1]
                avg_sims = []
                
                for j, id2 in enumerate(report_ids):
                    if i >= j or id2 in clustered:
                        continue
                    
                    # Check similarity (order-independent lookup)
                    key = (id1, id2) if id1 < id2 else (id2, id1)
                    if key in similarities:
                        cluster_members.append(id2)
                        avg_sims.append(similarities[key])
                
                # Only create group if we have multiple members
                if len(cluster_members) >= 2:
                    group_counter += 1
                    group = SemanticGroup(
                        group_id=f"SEM_{group_counter:04d}",
                        category=category,
                        avg_similarity=sum(avg_sims) / len(avg_sims) if avg_sims else 0.0,
                        member_ids=cluster_members,
                        member_names=[self.annotations[rid].report_name for rid in cluster_members]
                    )
                    self.semantic_groups.append(group)
                    clustered.update(cluster_members)
        
        self.stats['semantic_groups'] = len(self.semantic_groups)
        
        # Print summary by category
        cat_counts = {}
        for group in self.semantic_groups:
            cat_counts[group.category] = cat_counts.get(group.category, 0) + 1
        
        print(f"\n   📊 Semantic Groups by Category:")
        for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
            print(f"      {cat}: {count} groups")
        
        print(f"\n   ✅ Created {len(self.semantic_groups)} semantic groups")
        return len(self.semantic_groups)
    
    def step3_evaluate_groups(self) -> int:
        """
        Step 3: LLM evaluates each semantic group to identify true duplicates.
        Outputs per-report verdicts with rationale.
        """
        print(f"\n🔍 Step 3: Evaluating duplicate groups with LLM...")
        print(f"   Model: {TEXT_MODEL}")
        
        for i, group in enumerate(self.semantic_groups):
            print(f"   [{i+1}/{len(self.semantic_groups)}] Evaluating {group.group_id} ({len(group.member_ids)} reports)...", end=" ")
            
            try:
                # Build detailed comparison info
                reports_info = []
                for rid in group.member_ids:
                    ann = self.annotations[rid]
                    # Find original report info
                    report_info = next((r for r in self.reports if r.report_id == rid), None)
                    tables_str = ', '.join(report_info.tables[:10]) if report_info else 'N/A'
                    
                    reports_info.append(f"""
Report ID: {rid}
Report Name: {ann.report_name}
Narrative: {ann.narrative}
Business Purpose: {ann.business_purpose}
Key Entities: {', '.join(ann.key_entities)}
Tables: {tables_str}
""")
                
                reports_text = '\n---\n'.join(reports_info)
                
                prompt = f"""Analyze this group of semantically similar reports and determine which are TRUE duplicates.

Group ID: {group.group_id}
Category: {group.category}
Average Similarity: {group.avg_similarity:.2%}

Reports in this group:
{reports_text}

For each report, determine:
1. Is it a duplicate of another report in this group?
2. If yes, which report(s) is it a duplicate of?
3. Provide rationale for your decision

Also identify:
1. Which report should be the "canonical" (primary) version
2. Overall consolidation recommendation

Be conservative - only mark as duplicates if they truly serve the same business purpose."""

                response = self.client.responses.parse(
                    model=TEXT_MODEL,
                    input=[
                        {
                            "role": "system",
                            "content": "You are a data governance expert specializing in report consolidation. Analyze groups of similar reports and identify true duplicates vs. reports that serve different purposes despite similarities."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    text_format=GroupEvaluation,
                )
                
                self.stats['api_calls']['text_gen'] += 1
                
                evaluation = response.output_parsed
                evaluation.group_id = group.group_id  # Ensure group_id is set
                
                self.group_evaluations.append(evaluation)
                self.stats['evaluated_groups'] += 1
                
                # Count true duplicates
                dups = sum(1 for v in evaluation.verdicts if v.is_duplicate)
                self.stats['true_duplicates_found'] += dups
                
                print(f"✓ ({dups} duplicates)")
                
            except Exception as e:
                print(f"✗ Error: {e}")
                continue
        
        print(f"\n   ✅ Evaluated {self.stats['evaluated_groups']} groups")
        print(f"   🔄 Found {self.stats['true_duplicates_found']} true duplicates")
        return self.stats['evaluated_groups']
    
    def export_results(self, output_dir: str):
        """Export all results to JSON files."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        print(f"\n💾 Exporting results to: {output_path}")
        
        # 1. Report Annotations
        annotations_data = []
        for annotation in self.annotations.values():
            annotations_data.append({
                'report_id': annotation.report_id,
                'report_name': annotation.report_name,
                'narrative': annotation.narrative,
                'category': annotation.category,
                'key_entities': annotation.key_entities,
                'business_purpose': annotation.business_purpose,
                # Don't save embeddings - too large
            })
        
        with open(output_path / 'report_annotations.json', 'w') as f:
            json.dump(annotations_data, f, indent=2)
        print(f"   ✅ report_annotations.json ({len(annotations_data)} reports)")
        
        # 2. Semantic Groups
        groups_data = []
        for group in self.semantic_groups:
            groups_data.append({
                'group_id': group.group_id,
                'category': group.category,
                'avg_similarity': round(group.avg_similarity, 4),
                'member_count': len(group.member_ids),
                'member_ids': group.member_ids,
                'member_names': group.member_names
            })
        
        with open(output_path / 'semantic_groups.json', 'w') as f:
            json.dump(groups_data, f, indent=2)
        print(f"   ✅ semantic_groups.json ({len(groups_data)} groups)")
        
        # 3. Group Evaluations
        evaluations_data = []
        for eval in self.group_evaluations:
            verdicts_list = []
            for v in eval.verdicts:
                verdicts_list.append({
                    'report_id': v.report_id,
                    'report_name': v.report_name,
                    'is_duplicate': v.is_duplicate,
                    'duplicate_of': v.duplicate_of,
                    'rationale': v.rationale
                })
            
            evaluations_data.append({
                'group_id': eval.group_id,
                'overall_assessment': eval.overall_assessment,
                'canonical_report': eval.canonical_report,
                'canonical_rationale': eval.canonical_rationale,
                'verdicts': verdicts_list,
                'consolidation_recommendation': eval.consolidation_recommendation
            })
        
        with open(output_path / 'group_evaluations.json', 'w') as f:
            json.dump(evaluations_data, f, indent=2)
        print(f"   ✅ group_evaluations.json ({len(evaluations_data)} evaluations)")
        
        # 4. Summary
        summary = {
            'analysis_timestamp': datetime.now().isoformat(),
            'config': {
                'embedding_model': EMBEDDING_MODEL,
                'text_model': TEXT_MODEL,
                'similarity_threshold': self.similarity_threshold,
                'categories': REPORT_CATEGORIES
            },
            'stats': self.stats,
            'category_distribution': {},
            'duplicate_summary': {
                'total_groups_analyzed': self.stats['evaluated_groups'],
                'true_duplicates_found': self.stats['true_duplicates_found']
            }
        }
        
        # Category distribution
        for annotation in self.annotations.values():
            cat = annotation.category
            summary['category_distribution'][cat] = summary['category_distribution'].get(cat, 0) + 1
        
        with open(output_path / 'semantic_summary.json', 'w') as f:
            json.dump(summary, f, indent=2)
        print(f"   ✅ semantic_summary.json")
        
        # 5. Save embeddings separately (for future use)
        embeddings_data = {
            rid: ann.embedding 
            for rid, ann in self.annotations.items()
        }
        with open(output_path / 'embeddings.json', 'w') as f:
            json.dump(embeddings_data, f)
        print(f"   ✅ embeddings.json")
    
    def print_summary(self):
        """Print analysis summary."""
        print("\n" + "=" * 70)
        print("LLM SEMANTIC ANALYSIS SUMMARY")
        print("=" * 70)
        
        print(f"\n📊 CONFIGURATION:")
        print(f"   Embedding Model: {EMBEDDING_MODEL}")
        print(f"   Text Model: {TEXT_MODEL}")
        print(f"   Similarity Threshold: {self.similarity_threshold:.0%}")
        
        print(f"\n📈 STATISTICS:")
        print(f"   Total Reports: {self.stats['total_reports']}")
        print(f"   Annotated: {self.stats['annotated_reports']}")
        print(f"   Semantic Groups: {self.stats['semantic_groups']}")
        print(f"   Evaluated Groups: {self.stats['evaluated_groups']}")
        print(f"   True Duplicates: {self.stats['true_duplicates_found']}")
        
        print(f"\n🔌 API USAGE:")
        print(f"   Embedding Calls: {self.stats['api_calls']['embedding']}")
        print(f"   Text Generation Calls: {self.stats['api_calls']['text_gen']}")
        
        print(f"\n📁 CATEGORY DISTRIBUTION:")
        cat_counts = {}
        for ann in self.annotations.values():
            cat_counts[ann.category] = cat_counts.get(ann.category, 0) + 1
        for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
            pct = count / len(self.annotations) * 100 if self.annotations else 0
            print(f"   {cat}: {count} ({pct:.1f}%)")
        
        print("\n" + "=" * 70)


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='LLM Semantic Analyzer for SQL Reports',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python llm_semantic_analyzer.py output/temptestsql/direct_reports
  python llm_semantic_analyzer.py output/temptestsql/direct_reports -t 0.85
  python llm_semantic_analyzer.py output/temptestsql/direct_reports -o semantic_results
        """
    )
    parser.add_argument('sql_dir', help='Directory containing SQL files')
    parser.add_argument('-o', '--output', default=None,
                        help='Output directory (default: <parent>/semantic_analysis)')
    parser.add_argument('-t', '--threshold', type=float, default=0.80,
                        help='Similarity threshold for clustering (default: 0.80)')
    
    args = parser.parse_args()
    
    # Validate OPENAI_API_KEY
    if not os.environ.get('OPENAI_API_KEY'):
        print("❌ Error: OPENAI_API_KEY environment variable not set")
        print("   Set it with: $env:OPENAI_API_KEY = 'your-key-here'")
        return 1
    
    # Determine output directory
    output_dir = args.output or os.path.join(
        os.path.dirname(args.sql_dir), 'semantic_analysis'
    )
    
    print("=" * 70)
    print("LLM SEMANTIC ANALYZER")
    print("=" * 70)
    print(f"SQL Directory: {args.sql_dir}")
    print(f"Output Directory: {output_dir}")
    print(f"Similarity Threshold: {args.threshold:.0%}")
    print(f"Embedding Model: {EMBEDDING_MODEL}")
    print(f"Text Model: {TEXT_MODEL}")
    
    # Run analysis
    analyzer = LLMSemanticAnalyzer(args.sql_dir, args.threshold)
    
    # Step 1: Load reports
    if analyzer.load_reports() == 0:
        print("❌ No SQL files found")
        return 1
    
    # Step 2: Annotate with LLM
    analyzer.step1_annotate_reports()
    
    # Step 3: Semantic clustering
    analyzer.step2_cluster_semantically()
    
    # Step 4: Evaluate groups with LLM
    analyzer.step3_evaluate_groups()
    
    # Export results
    analyzer.export_results(output_dir)
    
    # Print summary
    analyzer.print_summary()
    
    print(f"\n✅ Analysis complete! Results saved to: {output_dir}")
    return 0


if __name__ == "__main__":
    exit(main())
