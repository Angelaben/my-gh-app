---
name: code-reviewer
description: Comprehensive code review skill for TypeScript, JavaScript, Python, Swift, Kotlin, Go. Includes automated code analysis, best practice checking, security scanning, and review checklist generation. Use when reviewing pull requests, providing code feedback, identifying issues, or ensuring code quality standards. Returns findings labelled P0/P1/P2/P3.
---

# Code Reviewer

Toolkit de code review structuré avec le formalisme de priorité **P0 → P2** et rapport HTML pour les PRs majeures.

---

## Système de priorité (OBLIGATOIRE)

| Niveau | Nom | Définition | Blocant pour merge ? |
|--------|-----|------------|----------------------|
| **P0** | Critique | Bug avéré, faille de sécurité, perte de données, crash en prod, migration cassée | **OUI — bloque le merge** |
| **P1** | Important | Mauvaise pratique sérieuse, bug potentiel, problème de perf significatif, breaking change non documenté | **Doit être traité avant merge** |
| **P2** | Nice-to-have | Amélioration de lisibilité, refacto légère, test manquant sur cas secondaire | **Recommandé, non bloquant** |
| **P3** | Nit | Style, micro-optimisation, documentation gap, renommage cosmétique | **Optionnel** |

---

## Modes de sortie

### Mode standard (PRs < 30 fichiers)

Chaque review DOIT suivre cette structure dans cet ordre :

1. **Résumé exécutif** (2-4 phrases)
2. **Tableau récapitulatif** (toujours présent, même si vide)
3. **Détail par commentaire** (fichier, ligne, problème, suggestion)
4. **Points positifs** (toujours inclure)
5. **Verdict** (APPROVE / REQUEST CHANGES / COMMENT)

### Mode rapport HTML (PRs > 30 fichiers OU architecture rewrite OU demandé)

Pour les PRs majeures (refacto d'architecture, >50 fichiers, changements workflow/pipeline), produire un rapport HTML exhaustif à la racine du projet (`<pr-topic>-report.html`). Le rapport DOIT inclure :

1. **Executive Summary** — stats (fichiers, lignes +/-), verdict global
2. **Architecture Changes** — before/after diagrams (ASCII), table comparative
3. **New Features & Capabilities** — chaque nouveau composant avec tags (NEW, REFACTOR, MIGRATION, PERF)
4. **P0/P1/P2 sections** — détail par issue avec fichier, ligne, code samples
5. **Breaking Changes table** — change, impact, migration path
6. **Regression Risk Matrix** — area, risk level (HIGH/MEDIUM/LOW), description, mitigation
7. **Test Coverage Analysis** — tests updated, critical gaps, lost coverage
8. **Verdict** — approve/request changes avec actions requises

Le rapport HTML doit utiliser un thème sombre professionnel et être self-contained (pas de dépendances externes).

---

## Analyse en profondeur (OBLIGATOIRE pour toute PR)

### 7 axes d'analyse

1. **Correctness** — logique, edge cases, null/undefined, off-by-one, type mismatches
2. **Sécurité** — injection, secrets hardcodés, auth, inputs non validés, raw SQL
3. **Performance** — N+1, boucles inutiles, appels bloquants, index manquants, concurrency config
4. **Maintenabilité** — nommage, longueur des fonctions, duplication, code mort, FIXME oubliés
5. **Tests** — couverture, cas limites, assertions faibles, features non testées
6. **Regressions** — fonctionnalités supprimées, changements de comportement silencieux, backward compat
7. **Data integrity** — migrations, upsert idempotency, race conditions, transaction boundaries

### Attention spéciale aux

- **Breaking changes non signalés** : renommages de champs API, suppression de routes, changements de schema de réponse
- **Config renames** : clés renommées sans alias/migration → environnements déployés cassent silencieusement
- **Migrations DB** : down_revision correct (pas de branch conflict Alembic), revision ID non-placeholder
- **Idempotency** : upserts qui créent des doublons au re-run, pas de dedup, pas de cleanup
- **Concurrency** : race conditions sur append/update, row locking manquant, mauvaise config concurrency
- **Temporal/Workflow specifics** : continue_as_new safety, event history growth, ParentClosePolicy, determinism

---

## Stratégie de review parallèle

Pour les PRs majeures, dispatcher des agents parallèles couvrant des axes indépendants :

1. **Architecture & Workflows** — rewrites de pipeline, orchestration, concurrency, continue_as_new
2. **Capabilities & Data Layer** — CRUD, upserts, migrations, SQL, entity changes
3. **Tests & Regression risks** — coverage gaps, removed tests, untested new features
4. **Config, API & Frontend** — breaking changes, route registration, config renames, Bruno sync

Chaque agent produit des findings par priorité. Les résultats sont consolidés dans le rapport final.

---

## Règles absolues

- **Ne jamais approuver une PR avec un P0**
- **Toujours produire le tableau récapitulatif** (format standard) ou rapport HTML (format exhaustif)
- **Toujours inclure les points positifs** — une review n'est pas que négative
- **Être précis** : fichier + ligne + pourquoi c'est un problème + suggestion de fix
- **Vérifier les interactions cross-fichiers** : un rename dans un capability → tous les consumers sont-ils mis à jour ?
- **Détecter les dead code et dead config** : champs ajoutés à la config mais jamais référencés dans le code
- **Vérifier la cohérence des types** : si le response model dit `dict[str, float]`, le code ne doit pas pouvoir produire `None` values

---

## Référence rapide

| Axe | Exemples P0 | Exemples P1 | Exemples P2 | Exemples P3 |
|-----|-------------|-------------|-------------|-------------|
| Correctness | Null check sur list (jamais None), paginate sans ORDER BY | Exception avalée silencieusement | Condition redondante | Commentaire obsolète |
| Sécurité | SQL injection, raw `text()` sans binding types | Token logué en clair | Validation partielle | Docstring imprécise |
| Performance | N+1 delete en boucle, pas de transaction wrap | Pas de pagination, mauvaise concurrency config | Cache non utilisé | Import inutilisé |
| Maintenabilité | FIXME laissé en prod | Fonction >100 lignes, route/request mismatch | Variable `x`, `tmp` | Renommage cosmétique |
| Tests | Aucun test sur capability critique, P0 bug non détectable | Cas d'erreur non testé | Assertion trop large | Nom de test peu explicite |
| Regressions | Suppression de fallback (SharePoint OCR), API breaking change | Config rename sans migration | Feature flags dead | Changelog manquant |
| Data integrity | Alembic branch conflict, upsert cross-run contamination | Append without dedup on re-run | Placeholder revision ID | Ordre de champs incohérent |

---

## Tech stack supporté

**Languages :** TypeScript, JavaScript, Python, Go, Swift, Kotlin
**Frontend :** React, Next.js, React Native
**Backend :** FastAPI, Node.js, Express, GraphQL, REST, Temporal/Mistral Workflows
**Database :** PostgreSQL, Alembic, Prisma, NeonDB, Supabase
**DevOps :** Docker, Kubernetes, GitHub Actions
**Cloud :** AWS, GCP, Azure

---

## Ressources complémentaires

- Checklist détaillée : `references/code_review_checklist.md`
- Standards de code : `references/coding_standards.md`
- Antipatterns communs : `references/common_antipatterns.md`
